"""Synchronisations-Service zwischen Schalti Termine und dem Fahrschulmanager (FSM).

Steuert den Abgleich von Belegungszeiten (Fahrstunden/Sperren aus FSM)
sowie das Eintragen und Stornieren von gebuchten Beratungsterminen.
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


def sync_blocker_fuer_fahrlehrer(
    fahrlehrer: Fahrlehrer,
    tage_voraus: int | None = None,
    client: FsmClient | None = None,
) -> int:
    """Synchronisiert Belegungszeiten aus FSM als Sperrzeiten in Schalti Termine.

    Liest alle Termine und Fahrstunden des Fahrlehrers aus FSM aus. Eigene, von
    Schalti Termine erstellte Beratungstermine werden übersprungen. Alle
    anderen Einträge werden als Sperrzeiten hinterlegt, um Doppelbuchungen zu
    verhindern.

    Liefert die Anzahl der aktuell aktiven FSM-Sperrzeiten zurück.
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

    # Bereits von Schalti erzeugte FSM-Termin-IDs ermitteln (um keine Selbst-Sperren zu erzeugen)
    eigene_fsm_ids = set(
        Termin.objects.filter(fahrlehrer=fahrlehrer)
        .exclude(fsm_termin_id="")
        .values_list("fsm_termin_id", flat=True)
    )

    gueltige_fsm_ids: set[str] = set()

    with transaction.atomic():
        for termin in fsm_termine:
            if not termin.id or termin.id in eigene_fsm_ids:
                continue

            # Sicherstellen, dass die Zeitzone korrekt ist
            beginn = timezone.localtime(termin.von) if timezone.is_aware(termin.von) else timezone.make_aware(termin.von)
            termin_ende = timezone.localtime(termin.bis) if timezone.is_aware(termin.bis) else timezone.make_aware(termin.bis)

            if termin_ende <= beginn:
                continue

            grund = f"FSM: {termin.terminart}"
            if termin.titel:
                grund = f"FSM: {termin.titel[:150]}"

            sperrzeit, _ = Sperrzeit.objects.update_or_create(
                fahrlehrer=fahrlehrer,
                fsm_id=termin.id,
                defaults={
                    "beginn": beginn,
                    "ende": termin_ende,
                    "grund": grund,
                },
            )
            gueltige_fsm_ids.add(termin.id)

        # Nicht mehr in FSM vorhandene Sperrzeiten im Zeitraum aufräumen
        geloescht_count, _ = (
            Sperrzeit.objects.filter(
                fahrlehrer=fahrlehrer,
                beginn__gte=jetzt,
                beginn__lte=ende,
            )
            .exclude(fsm_id="")
            .exclude(fsm_id__in=gueltige_fsm_ids)
            .delete()
        )

        if geloescht_count > 0:
            logger.info(
                "FSM-Sync: %s veraltete Sperrzeiten für Fahrlehrer %s entfernt",
                geloescht_count,
                fahrlehrer.pk,
            )

    return len(gueltige_fsm_ids)


def sync_alle_fahrlehrer(client: FsmClient | None = None) -> dict[int, int]:
    """Führt den Blocker-Sync für alle aktiven Fahrlehrer mit FSM-Verknüpfung aus."""
    ergebnisse: dict[int, int] = {}
    client = client or FsmClient()

    if not getattr(settings, "FSM_SYNC_ENABLED", False):
        return ergebnisse

    for fahrlehrer in Fahrlehrer.objects.filter(aktiv=True).exclude(fsm_id=""):
        if fahrlehrer.fsm_sync_aktiv:
            anzahl = sync_blocker_fuer_fahrlehrer(fahrlehrer, client=client)
            ergebnisse[fahrlehrer.pk] = anzahl

    return ergebnisse


def buche_in_fsm(buchung: Buchung, client: FsmClient | None = None) -> str | None:
    """Legt einen gebuchten Beratungstermin im Kalender des FSM an.

    Speichert die erzeugte FSM-Termin-UUID am Termin-Datensatz ab.
    """
    fahrlehrer = buchung.termin.fahrlehrer
    if not is_fsm_aktiv_fuer_fahrlehrer(fahrlehrer):
        return None

    client = client or FsmClient()

    titel = f"Beratung: {buchung.name}"
    if buchung.telefon:
        titel += f" ({buchung.telefon})"
    elif buchung.email:
        titel += f" ({buchung.email})"

    try:
        fsm_id = client.termin_anlegen(
            fahrlehrer_fsm_id=fahrlehrer.fsm_id,
            von=buchung.termin.beginn,
            bis=buchung.termin.ende,
            titel=titel,
        )
        buchung.termin.fsm_termin_id = fsm_id
        buchung.termin.save(update_fields=["fsm_termin_id", "geaendert_am"])
        logger.info(
            "FSM-Sync: Buchung %s in FSM eingetragen (FSM-ID %s)",
            buchung.referenz,
            fsm_id,
        )
        return fsm_id
    except FsmError as exc:
        logger.error(
            "FSM-Sync: Fehler beim Eintragen der Buchung %s in FSM: %s",
            buchung.referenz,
            exc,
        )
        return None


def storniere_in_fsm(buchung: Buchung, client: FsmClient | None = None) -> bool:
    """Entfernt einen stornierten Beratungstermin aus dem FSM-Kalender."""
    fsm_id = buchung.termin.fsm_termin_id
    if not fsm_id:
        return False

    client = client or FsmClient()

    try:
        client.termin_loeschen(fsm_id)
        buchung.termin.fsm_termin_id = ""
        buchung.termin.save(update_fields=["fsm_termin_id", "geaendert_am"])
        logger.info(
            "FSM-Sync: Termin für Buchung %s aus FSM gelöscht (FSM-ID %s)",
            buchung.referenz,
            fsm_id,
        )
        return True
    except FsmError as exc:
        logger.error(
            "FSM-Sync: Fehler beim Löschen des Termins %s aus FSM: %s",
            fsm_id,
            exc,
        )
        return False
