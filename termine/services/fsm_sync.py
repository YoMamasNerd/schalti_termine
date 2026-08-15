"""Synchronisations-Service zwischen Schalti Termine und dem Fahrschulmanager (FSM).

Steuert den Abgleich von Belegungszeiten (Fahrstunden/Sperren aus FSM)
sowie das Vorab-Blockieren, Eintragen, Aktualisieren und Löschen von
Beratungsterminen im FSM-Kalender.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import Fahrlehrer, Sperrzeit, Termin
from .fsm_client import FsmClient, FsmError

if TYPE_CHECKING:
    from ..models import Buchung

logger = logging.getLogger(__name__)


def is_fsm_aktiv_fuer_fahrlehrer(fahrlehrer: Fahrlehrer) -> bool:
    """Prüft, ob FSM-Sync global und für diesen spezifischen Fahrlehrer aktiv ist."""
    global_aktiv = getattr(settings, "FSM_SYNC_ENABLED", False)
    return bool(global_aktiv and fahrlehrer.fsm_sync_aktiv and fahrlehrer.fsm_id)


def exportiere_termin_nach_fsm(
    termin: Termin,
    client: FsmClient | None = None,
) -> str | None:
    """Blockiert oder aktualisiert einen Beratungstermin im FSM-Kalender.

    Wird aufgerufen für:
    - Freie Termine (Vorab-Blocker im FSM-Kalender)
    - Gebuchte Termine (mit Kundenname und Telefon/E-Mail)
    - Stornierte Termine (Rückkehr zum freien Vorab-Blocker)
    """
    fahrlehrer = termin.fahrlehrer
    if not is_fsm_aktiv_fuer_fahrlehrer(fahrlehrer):
        return None

    client = client or FsmClient()

    if termin.status == Termin.Status.GEBUCHT:
        buchung = termin.aktive_buchung
        if buchung:
            kontakt = buchung.telefon or buchung.email
            titel = f"Beratung: {buchung.name}" + (f" ({kontakt})" if kontakt else "")
        else:
            titel = f"Beratung: {termin.terminart.name} (gebucht)"
    else:
        titel = f"Beratung: {termin.terminart.name} (frei)"

    try:
        if termin.fsm_termin_id:
            # Bestehenden FSM-Eintrag aktualisieren
            erfolg = client.termin_aktualisieren(
                fsm_termin_id=termin.fsm_termin_id,
                fahrlehrer_fsm_id=fahrlehrer.fsm_id,
                von=termin.beginn,
                bis=termin.ende,
                titel=titel,
            )
            if erfolg:
                logger.info(
                    "FSM-Sync: Termin %s in FSM aktualisiert (%s)",
                    termin.pk,
                    termin.fsm_termin_id,
                )
                return termin.fsm_termin_id

        # Neu in FSM anlegen
        fsm_id = client.termin_anlegen(
            fahrlehrer_fsm_id=fahrlehrer.fsm_id,
            von=termin.beginn,
            bis=termin.ende,
            titel=titel,
        )
        termin.fsm_termin_id = fsm_id
        termin.save(update_fields=["fsm_termin_id", "geaendert_am"])
        logger.info(
            "FSM-Sync: Termin %s in FSM angelegt (FSM-ID %s)",
            termin.pk,
            fsm_id,
        )
        return fsm_id
    except FsmError as exc:
        logger.warning(
            "FSM-Sync: Konnte Termin %s nicht in FSM synchronisieren: %s",
            termin.pk,
            exc,
        )
        return None


def loesche_fsm_termin_by_id(fsm_termin_id: str, client: FsmClient | None = None) -> bool:
    """Löscht einen Termin anhand seiner FSM-Termin-UUID aus dem FSM-Kalender."""
    if not fsm_termin_id:
        return False

    client = client or FsmClient()
    try:
        client.termin_loeschen(fsm_termin_id)
        logger.info("FSM-Sync: FSM-Termin %s gelöscht", fsm_termin_id)
        return True
    except FsmError as exc:
        logger.warning("FSM-Sync: Konnte FSM-Termin %s nicht löschen: %s", fsm_termin_id, exc)
        return False


def loesche_termin_aus_fsm(termin: Termin, client: FsmClient | None = None) -> bool:
    """Entfernt die FSM-Sperre für einen gelöschten Schalti-Termin."""
    fsm_id = termin.fsm_termin_id
    if not fsm_id:
        return False

    erfolg = loesche_fsm_termin_by_id(fsm_id, client=client)
    if erfolg:
        termin.fsm_termin_id = ""
        termin.save(update_fields=["fsm_termin_id", "geaendert_am"])
    return erfolg


def buche_in_fsm(buchung: Buchung, client: FsmClient | None = None) -> str | None:
    """Hook nach erfolgreicher Buchungsbestätigung: Schreibt Kundendaten in FSM."""
    return exportiere_termin_nach_fsm(buchung.termin, client=client)


def storniere_in_fsm(buchung: Buchung, client: FsmClient | None = None) -> bool:
    """Hook nach Stornierung: Setzt FSM-Termin auf frei zurück oder löscht ihn."""
    termin = buchung.termin
    if termin.beginn > timezone.now() and termin.status == Termin.Status.FREI:
        # Zukünftiger Termin bleibt frei im Angebot -> FSM-Eintrag auf frei zurücksetzen
        fsm_id = exportiere_termin_nach_fsm(termin, client=client)
        return bool(fsm_id)
    # Vergangener Termin oder nicht mehr im Angebot -> aus FSM entfernen
    return loesche_termin_aus_fsm(termin, client=client)


def sync_blocker_fuer_fahrlehrer(
    fahrlehrer: Fahrlehrer,
    tage_voraus: int | None = None,
    client: FsmClient | None = None,
) -> int:
    """Führt den bidirektionalen Abgleich für einen Fahrlehrer durch:

    1. Liest Termine und Fahrstunden aus FSM aus.
    2. Fremde FSM-Termine werden als `Sperrzeit` in Schalti Termine hinterlegt.
    3. Zukünftige freie/gebuchte Schalti-Termine werden in FSM geblockt.
    4. Wenn ein FSM-Blocker in FSM manuell gelöscht wurde, wird der freie Termin
       in Schalti Termine ebenfalls entfernt.
    """
    if not is_fsm_aktiv_fuer_fahrlehrer(fahrlehrer):
        return 0

    client = client or FsmClient()
    tage = tage_voraus or (fahrlehrer.horizont_wochen * 7)

    jetzt = timezone.now()
    ende = jetzt + dt.timedelta(days=tage)

    try:
        fsm_termine = client.get_termine(fahrlehrer.fsm_id, jetzt, ende)
    except FsmError as exc:
        logger.warning(
            "FSM-Sync: Konnte Termine für Fahrlehrer %s nicht abrufen: %s",
            fahrlehrer.pk,
            exc,
        )
        return 0

    fsm_termin_dict = {t.id: t for t in fsm_termine if t.id}

    # 1. Zukünftige Schalti-Termine mit FSM abgleichen
    schalti_termine = Termin.objects.filter(
        fahrlehrer=fahrlehrer,
        beginn__gte=jetzt,
        beginn__lte=ende,
    ).exclude(status=Termin.Status.ENTFALLEN)

    for termin in schalti_termine:
        if termin.fsm_termin_id:
            if termin.fsm_termin_id not in fsm_termin_dict:
                # Wurde in FSM manuell gelöscht -> in Schalti freigeben/entfernen wenn ungebucht
                if termin.status == Termin.Status.FREI:
                    logger.info(
                        "FSM-Sync: Freier Termin %s wurde in FSM gelöscht -> wird in Schalti entfernt",
                        termin.pk,
                    )
                    termin.delete()
                    continue
        else:
            # Hat noch keinen FSM-Blocker -> in FSM anlegen
            exportiere_termin_nach_fsm(termin, client=client)

    # Bereits von Schalti erzeugte FSM-IDs
    eigene_fsm_ids = set(
        Termin.objects.filter(fahrlehrer=fahrlehrer)
        .exclude(fsm_termin_id="")
        .values_list("fsm_termin_id", flat=True)
    )

    gueltige_fsm_sperren: set[str] = set()

    # 2. Fremde FSM-Events als Sperrzeiten hinterlegen
    with transaction.atomic():
        for fsm_id, termin in fsm_termin_dict.items():
            if fsm_id in eigene_fsm_ids:
                continue

            beginn = timezone.localtime(termin.von) if timezone.is_aware(termin.von) else timezone.make_aware(termin.von)
            termin_ende = timezone.localtime(termin.bis) if timezone.is_aware(termin.bis) else timezone.make_aware(termin.bis)

            if termin_ende <= beginn:
                continue

            grund = f"FSM: {termin.terminart}"
            if termin.titel:
                grund = f"FSM: {termin.titel[:150]}"

            Sperrzeit.objects.update_or_create(
                fahrlehrer=fahrlehrer,
                fsm_id=fsm_id,
                defaults={
                    "beginn": beginn,
                    "ende": termin_ende,
                    "grund": grund,
                },
            )
            gueltige_fsm_sperren.add(fsm_id)

        # Nicht mehr vorhandene Sperrzeiten bereinigen
        Sperrzeit.objects.filter(
            fahrlehrer=fahrlehrer,
            beginn__gte=jetzt,
            beginn__lte=ende,
        ).exclude(fsm_id="").exclude(fsm_id__in=gueltige_fsm_sperren).delete()

    return len(gueltige_fsm_sperren)


def sync_alle_fahrlehrer(client: FsmClient | None = None) -> dict[int, int]:
    """Führt den Sync für alle aktiven Fahrlehrer mit FSM-Verknüpfung aus."""
    ergebnisse: dict[int, int] = {}
    client = client or FsmClient()

    if not getattr(settings, "FSM_SYNC_ENABLED", False):
        return ergebnisse

    for fahrlehrer in Fahrlehrer.objects.filter(aktiv=True).exclude(fsm_id=""):
        if fahrlehrer.fsm_sync_aktiv:
            anzahl = sync_blocker_fuer_fahrlehrer(fahrlehrer, client=client)
            ergebnisse[fahrlehrer.pk] = anzahl

    return ergebnisse
