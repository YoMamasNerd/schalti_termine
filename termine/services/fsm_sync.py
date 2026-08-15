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

from ..models import Fahrlehrer, FahrschulEinstellungen, Sperrzeit, Termin
from .fsm_client import FsmClient, FsmError

if TYPE_CHECKING:
    from ..models import Buchung

logger = logging.getLogger(__name__)


def is_fsm_aktiv_fuer_fahrlehrer(fahrlehrer: Fahrlehrer) -> bool:
    """Prüft, ob FSM-Sync global und für diesen spezifischen Fahrlehrer aktiv ist."""
    global_aktiv = getattr(settings, "FSM_SYNC_ENABLED", False)
    return bool(global_aktiv and fahrlehrer.fsm_sync_aktiv and fahrlehrer.fsm_id)


def importiere_fahrlehrer_aus_fsm(
    client: FsmClient | None = None,
) -> tuple[list[Fahrlehrer], list[Fahrlehrer]]:
    """Liest alle aktiven Fahrlehrer aus FSM aus und legt sie in Schalti Termine an
    bzw. verknüpft sie anhand von FSM-ID oder Name.

    Gibt (neu_erstellte, aktualisierte_oder_verknuepfte) zurück.
    """
    if not getattr(settings, "FSM_SYNC_ENABLED", False):
        return [], []

    client = client or FsmClient()
    fsm_lehrer = client.get_fahrlehrer()

    neu_erstellt: list[Fahrlehrer] = []
    aktualisiert: list[Fahrlehrer] = []

    for entry in fsm_lehrer:
        if not isinstance(entry, dict):
            continue

        fsm_id = str(entry.get("id") or "").strip()
        if not fsm_id:
            continue

        vorname = str(entry.get("vorname") or "").strip()
        nachname = str(entry.get("nachname") or "").strip()
        if vorname and nachname:
            voller_name = f"{vorname} {nachname}"
        else:
            voller_name = str(entry.get("displayName") or entry.get("name") or "Fahrlehrer").strip()

        email = str(entry.get("email") or "").strip()
        if not email:
            fallback = getattr(settings, "DEFAULT_FROM_EMAIL", "mail@fahrschule-schaltwerk.de")
            if "<" in fallback and ">" in fallback:
                email = fallback.split("<")[1].replace(">", "").strip()
            else:
                email = fallback

        telefon = str(entry.get("mobil") or entry.get("telefon") or "").strip()

        # 1. Prüfe auf bestehende FSM-ID
        lehrer = Fahrlehrer.objects.filter(fsm_id=fsm_id).first()

        # 2. Wenn nicht gefunden, suche nach passendem Namen
        if not lehrer:
            lehrer = Fahrlehrer.objects.filter(name__iexact=voller_name).first()

        if lehrer:
            geaendert = False
            if lehrer.fsm_id != fsm_id:
                lehrer.fsm_id = fsm_id
                geaendert = True
            if not lehrer.fsm_sync_aktiv:
                lehrer.fsm_sync_aktiv = True
                geaendert = True
            if telefon and not lehrer.telefon:
                lehrer.telefon = telefon
                geaendert = True
            if geaendert:
                lehrer.save()
            aktualisiert.append(lehrer)
        else:
            # Neu anlegen
            neuer_fl = Fahrlehrer.objects.create(
                name=voller_name,
                email=email,
                telefon=telefon,
                fsm_id=fsm_id,
                fsm_sync_aktiv=True,
                aktiv=True,
                bundesland=getattr(settings, "DEFAULT_BUNDESLAND", None) or FahrschulEinstellungen.get_solo().bundesland,
                vorlauf_stunden=24,
                horizont_wochen=4,
            )
            neu_erstellt.append(neuer_fl)

    return neu_erstellt, aktualisiert


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
            try:
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
            except FsmError as update_exc:
                logger.info(
                    "FSM-Sync: Aktualisierung von Termin %s (FSM-ID %s) fehlgeschlagen (%s) -> erstelle neuen Eintrag",
                    termin.pk,
                    termin.fsm_termin_id,
                    update_exc,
                )

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


def zerlege_zeitraum_fuer_fsm(
    beginn: dt.datetime,
    ende: dt.datetime,
    max_minuten: int = 600,
) -> list[tuple[dt.datetime, dt.datetime]]:
    """Zerlegt einen Sperrzeit-Zeitraum in FSM-kompatible Abschnitte.

    Ganztägige Sperrzeiten (z. B. 00:00 bis 23:59:59 oder mehrtägige Urlaube)
    werden pro Tag als genau ein 600-minütiger Block von 08:00 bis 18:00 Uhr
    eingetragen, damit der gesamte Arbeitstag blockiert ist und keine Mehrfachblöcke entstehen.
    """
    if beginn >= ende:
        return []

    beginn_local = timezone.localtime(beginn) if timezone.is_aware(beginn) else beginn
    ende_local = timezone.localtime(ende) if timezone.is_aware(ende) else ende

    # Ganztägige Sperrzeit prüfen (startet um Mitternacht / endet um 23:59)
    ist_ganztaegig = (
        beginn_local.hour == 0 and beginn_local.minute == 0 and
        (ende_local.hour >= 23 and ende_local.minute >= 59 or (ende_local - beginn_local).total_seconds() >= 86300)
    )

    abschnitte: list[tuple[dt.datetime, dt.datetime]] = []

    if ist_ganztaegig:
        tag_cursor = beginn_local.date()
        letzter_tag = ende_local.date()

        while tag_cursor <= letzter_tag:
            t_start_local = dt.datetime.combine(tag_cursor, dt.time(8, 0))
            t_ende_local = dt.datetime.combine(tag_cursor, dt.time(18, 0))
            t_start = timezone.make_aware(t_start_local) if timezone.is_aware(beginn) else t_start_local
            t_ende = timezone.make_aware(t_ende_local) if timezone.is_aware(beginn) else t_ende_local
            abschnitte.append((t_start, t_ende))
            tag_cursor += dt.timedelta(days=1)
    else:
        # Konkrete Uhrzeiten: Tageweise Blöcke mit maximal max_minuten Länge
        curr = beginn
        while curr < ende:
            curr_local = timezone.localtime(curr) if timezone.is_aware(curr) else curr
            naechster_tag_local = curr_local.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
            naechster_tag = timezone.make_aware(naechster_tag_local) if timezone.is_naive(naechster_tag_local) else naechster_tag_local

            tages_ende = min(naechster_tag, ende)
            if tages_ende <= curr:
                curr = curr + dt.timedelta(seconds=1)
                continue

            block_start = curr
            while block_start < tages_ende:
                block_ende = min(block_start + dt.timedelta(minutes=max_minuten), tages_ende)
                if (block_ende - block_start).total_seconds() >= 60:
                    abschnitte.append((block_start, block_ende))
                block_start = block_ende

            curr = tages_ende

    return abschnitte


def exportiere_sperrzeit_nach_fsm(
    sperre: Sperrzeit,
    client: FsmClient | None = None,
) -> list[str]:
    """Exportiert eine Sperrzeit (Urlaub/Abwesenheit oder Privat) nach FSM.

    - Sperrzeit.Typ.PRIVAT -> FSM-Terminart 'PP' (blockiert Kalender, zählt NICHT als Arbeitszeit).
    - Sperrzeit.Typ.SONSTIGE -> FSM-Terminart 'ST' (Sonstige Tätigkeit / Urlaub, zählt als Arbeitszeit).

    Ganztägige Urlaube werden ab 08:00 bis 18:00 Uhr (10h = 600 min) pro Tag eingetragen.
    Speichert die IDs kommagetrennt im Feld `fsm_id` der Sperrzeit.
    """
    if not is_fsm_aktiv_fuer_fahrlehrer(sperre.fahrlehrer):
        return []

    client = client or FsmClient()
    abschnitte = zerlege_zeitraum_fuer_fsm(sperre.beginn, sperre.ende, max_minuten=600)
    if not abschnitte:
        return []

    if getattr(sperre, "typ", Sperrzeit.Typ.SONSTIGE) == Sperrzeit.Typ.PRIVAT:
        terminart = getattr(settings, "FSM_PRIVAT_TERMINART", "PP")
        titel = sperre.grund or "Privat"
    else:
        terminart = getattr(settings, "FSM_SONSTIGE_TAETIGKEIT_TERMINART", "ST")
        titel = f"Sonstige Tätigkeit: {sperre.grund}" if sperre.grund else "Sonstige Tätigkeit"

    erstellte_ids: list[str] = []
    for von_dt, bis_dt in abschnitte:
        try:
            fsm_id = client.termin_anlegen(
                fahrlehrer_fsm_id=sperre.fahrlehrer.fsm_id,
                von=von_dt,
                bis=bis_dt,
                titel=titel,
                terminart=terminart,
            )
            if fsm_id:
                erstellte_ids.append(fsm_id)
        except FsmError as exc:
            logger.warning(
                "FSM-Sync: Konnte Sperrzeit (%s - %s) für %s nicht in FSM eintragen: %s",
                von_dt,
                bis_dt,
                sperre.fahrlehrer,
                exc,
            )

    if erstellte_ids:
        bisherige = [fid.strip() for fid in sperre.fsm_id.split(",") if fid.strip()]
        kombiniert = list(dict.fromkeys(bisherige + erstellte_ids))
        sperre.fsm_id = ",".join(kombiniert)
        sperre.save(update_fields=["fsm_id"])

    return erstellte_ids


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
    termin.refresh_from_db()
    if termin.beginn > timezone.now() and termin.status == Termin.Status.FREI:
        # Zukünftiger Termin bleibt frei im Angebot -> FSM-Eintrag auf frei zurücksetzen
        fsm_id = exportiere_termin_nach_fsm(termin, client=client)
        return bool(fsm_id)
    # Vergangener Termin oder nicht mehr im Angebot -> aus FSM entfernen
    return loesche_termin_aus_fsm(termin, client=client)


def sync_fahrlehrer_termine(
    fahrlehrer: Fahrlehrer,
    tage_voraus: int | None = None,
    client: FsmClient | None = None,
) -> int:
    """Exportiert alle noch nicht in FSM vorhandenen freien Beratungstermine."""
    if not is_fsm_aktiv_fuer_fahrlehrer(fahrlehrer):
        return 0

    from ..models import FahrschulEinstellungen

    client = client or FsmClient()
    wochen = FahrschulEinstellungen.get_solo().horizont_wochen or 4
    tage = tage_voraus or (wochen * 7)
    jetzt = timezone.now()
    ende = jetzt + dt.timedelta(days=tage)

    termine = Termin.objects.filter(
        fahrlehrer=fahrlehrer,
        beginn__gte=jetzt,
        beginn__lte=ende,
        status__in=[Termin.Status.FREI, Termin.Status.GEBUCHT],
    )
    count = 0
    for termin in termine:
        fsm_id = exportiere_termin_nach_fsm(termin, client=client)
        if fsm_id:
            count += 1
    return count


def is_theorie_termin(terminart: str, titel: str) -> bool:
    """Erkennt, ob ein FSM-Termin ein Theorieunterricht ist."""
    art = (terminart or "").strip().upper()
    tit = (titel or "").strip().lower()
    if art in ("PT", "TH", "THEORIE"):
        return True
    if any(kw in tit for kw in ("theorie", "th-grundstoff", "th-zusatzstoff", "grundstoff", "zusatzstoff")):
        return True
    return False


def sync_blocker_fuer_fahrlehrer(
    fahrlehrer: Fahrlehrer,
    tage_voraus: int | None = None,
    client: FsmClient | None = None,
    return_theorie: bool = False,
) -> int | tuple[int, list[tuple[str, dt.datetime, dt.datetime, str, Fahrlehrer]]]:
    """Führt den bidirektionalen Abgleich für einen Fahrlehrer durch:

    1. Liest Termine und Fahrstunden aus FSM aus.
    2. Fremde FSM-Termine werden als `Sperrzeit` in Schalti Termine hinterlegt.
    3. Zukünftige freie/gebuchte Schalti-Termine werden in FSM geblockt.
    4. Wenn ein FSM-Blocker in FSM manuell gelöscht wurde, wird der freie Termin
       in Schalti Termine ebenfalls entfernt.
    """
    if not is_fsm_aktiv_fuer_fahrlehrer(fahrlehrer):
        return (0, []) if return_theorie else 0

    from ..models import FahrschulEinstellungen

    client = client or FsmClient()
    wochen = FahrschulEinstellungen.get_solo().horizont_wochen or 4
    tage = tage_voraus or (wochen * 7)

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
        return (0, []) if return_theorie else 0

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
                    from .planung import termine_entfernen

                    termine_entfernen(Termin.objects.filter(pk=termin.pk))
                    continue
        else:
            # Hat noch keinen FSM-Blocker -> in FSM anlegen
            exportiere_termin_nach_fsm(termin, client=client)

    # Bereits von Schalti erzeugte FSM-IDs (Beratungstermine und eigene Sperrzeiten)
    eigene_fsm_ids = set(
        Termin.objects.filter(fahrlehrer=fahrlehrer)
        .exclude(fsm_termin_id="")
        .values_list("fsm_termin_id", flat=True)
    )
    for raw_fsm_id in (
        Sperrzeit.objects.filter(fahrlehrer=fahrlehrer, herkunft=Sperrzeit.Herkunft.MANUELL)
        .exclude(fsm_id="")
        .values_list("fsm_id", flat=True)
    ):
        for fid in raw_fsm_id.split(","):
            fid_clean = fid.strip()
            if fid_clean:
                eigene_fsm_ids.add(fid_clean)

    # Eigene manuelle Sperrzeiten ohne FSM-ID nach FSM exportieren
    manuelle_sperren = Sperrzeit.objects.filter(
        fahrlehrer=fahrlehrer,
        herkunft=Sperrzeit.Herkunft.MANUELL,
        beginn__gte=jetzt,
        beginn__lte=ende,
        fsm_id="",
    )
    for s in manuelle_sperren:
        created_ids = exportiere_sperrzeit_nach_fsm(s, client=client)
        eigene_fsm_ids.update(created_ids)

    gueltige_fsm_sperren: set[str] = set()
    theorie_termine: list[tuple[str, dt.datetime, dt.datetime, str, Fahrlehrer]] = []

    # 2. Fremde FSM-Events als Sperrzeiten hinterlegen
    from django.utils.html import strip_tags

    with transaction.atomic():
        for fsm_id, termin in fsm_termin_dict.items():
            if fsm_id in eigene_fsm_ids:
                continue

            beginn = timezone.localtime(termin.von) if timezone.is_aware(termin.von) else timezone.make_aware(termin.von)
            termin_ende = timezone.localtime(termin.bis) if timezone.is_aware(termin.bis) else timezone.make_aware(termin.bis)

            if termin_ende <= beginn:
                continue

            grund = f"FSM: {termin.terminart}"
            bereinigt = ""
            if termin.titel:
                bereinigt = " ".join(strip_tags(termin.titel).split())
                grund = f"FSM: {bereinigt[:150]}"

            Sperrzeit.objects.update_or_create(
                fahrlehrer=fahrlehrer,
                fsm_id=fsm_id,
                defaults={
                    "beginn": beginn,
                    "ende": termin_ende,
                    "grund": grund,
                    "herkunft": Sperrzeit.Herkunft.FSM,
                },
            )
            gueltige_fsm_sperren.add(fsm_id)

            if is_theorie_termin(termin.terminart, termin.titel or ""):
                theorie_termine.append((fsm_id, beginn, termin_ende, bereinigt or termin.terminart, fahrlehrer))

        # Nicht mehr vorhandene fremde Sperrzeiten bereinigen
        Sperrzeit.objects.filter(
            fahrlehrer=fahrlehrer,
            herkunft=Sperrzeit.Herkunft.FSM,
            beginn__gte=jetzt,
            beginn__lte=ende,
        ).exclude(fsm_id="").exclude(fsm_id__in=gueltige_fsm_sperren).delete()

    if return_theorie:
        return len(gueltige_fsm_sperren), theorie_termine
    return len(gueltige_fsm_sperren)


def sync_alle_fahrlehrer(client: FsmClient | None = None) -> dict[int, int]:
    """Führt den Sync für alle aktiven Fahrlehrer mit FSM-Verknüpfung aus."""
    ergebnisse: dict[int, int] = {}
    client = client or FsmClient()

    if not getattr(settings, "FSM_SYNC_ENABLED", False):
        return ergebnisse

    from ..models import FahrschulEinstellungen
    einstellungen = FahrschulEinstellungen.get_solo()
    theorie_beachten = einstellungen.fsm_theorie_blockiert_beratung

    aktive_lehrer = list(Fahrlehrer.objects.filter(aktiv=True).exclude(fsm_id=""))
    theorie_termine_gesammelt: list[tuple[str, dt.datetime, dt.datetime, str, Fahrlehrer]] = []

    for fahrlehrer in aktive_lehrer:
        if fahrlehrer.fsm_sync_aktiv:
            res = sync_blocker_fuer_fahrlehrer(fahrlehrer, client=client, return_theorie=True)
            if isinstance(res, tuple):
                anzahl, th_termine = res
            else:
                anzahl, th_termine = res, []
            ergebnisse[fahrlehrer.pk] = anzahl
            if theorie_beachten:
                theorie_termine_gesammelt.extend(th_termine)

    # Theorieunterricht raumweit für alle anderen Fahrlehrer blockieren
    gueltige_theorie_sperren: set[str] = set()

    if theorie_beachten and theorie_termine_gesammelt:
        from .planung import termine_entfernen

        alle_fahrschul_lehrer = list(Fahrlehrer.objects.filter(aktiv=True))
        for fsm_id, von, bis, titel, lehrer_owner in theorie_termine_gesammelt:
            theorie_fsm_id = f"theorie_{fsm_id}"
            gueltige_theorie_sperren.add(theorie_fsm_id)

            for ziel_lehrer in alle_fahrschul_lehrer:
                if ziel_lehrer.pk == lehrer_owner.pk:
                    continue  # Hat den Blocker bereits direkt erhalten

                grund_text = f"Theorieunterricht (Raum belegt): {titel}" if titel else "Theorieunterricht (Raum belegt)"
                Sperrzeit.objects.update_or_create(
                    fahrlehrer=ziel_lehrer,
                    fsm_id=theorie_fsm_id,
                    defaults={
                        "beginn": von,
                        "ende": bis,
                        "grund": grund_text[:200],
                        "typ": Sperrzeit.Typ.SONSTIGE,
                        "herkunft": Sperrzeit.Herkunft.FSM,
                    },
                )
                # Freie Termine im gesperrten Raum abräumen
                termine_entfernen(
                    Termin.objects.filter(
                        fahrlehrer=ziel_lehrer,
                        status=Termin.Status.FREI,
                        beginn__lt=bis,
                        ende__gt=von,
                    )
                )

    # Veraltete oder bei deaktivierter Option noch vorhandene Raum-Theorie-Sperren bereinigen
    jetzt = timezone.now()
    Sperrzeit.objects.filter(
        herkunft=Sperrzeit.Herkunft.FSM,
        fsm_id__startswith="theorie_",
        beginn__gte=jetzt - dt.timedelta(days=1),
    ).exclude(fsm_id__in=gueltige_theorie_sperren).delete()

    return ergebnisse


# --- Asynchrone Task-Handler (django-q) --------------------------------------


def _fsm_sync_termin_task(termin_id: int):
    """Hintergrund-Task zum Exportieren/Aktualisieren eines Termins in FSM."""
    try:
        termin = Termin.objects.select_related("fahrlehrer", "terminart").get(pk=termin_id)
    except Termin.DoesNotExist:
        return None
    return exportiere_termin_nach_fsm(termin)


def _fsm_sync_sperrzeit_task(sperrzeit_id: int) -> list[str]:
    """Hintergrund-Task zum Exportieren einer Sperrzeit in FSM als 'Sonstige Tätigkeit'."""
    try:
        sperre = Sperrzeit.objects.select_related("fahrlehrer").get(pk=sperrzeit_id)
    except Sperrzeit.DoesNotExist:
        return []
    return exportiere_sperrzeit_nach_fsm(sperre)


def _fsm_storno_task(termin_id: int):
    """Hintergrund-Task nach einer Stornierung."""
    try:
        termin = Termin.objects.select_related("fahrlehrer", "terminart").get(pk=termin_id)
    except Termin.DoesNotExist:
        return False
    if termin.beginn > timezone.now() and termin.status == Termin.Status.FREI:
        return bool(exportiere_termin_nach_fsm(termin))
    return loesche_termin_aus_fsm(termin)


def _fsm_sync_fahrlehrer_task(fahrlehrer_id: int):
    """Hintergrund-Task zum Abgleich aller Termine eines Fahrlehrers."""
    try:
        fahrlehrer = Fahrlehrer.objects.get(pk=fahrlehrer_id)
    except Fahrlehrer.DoesNotExist:
        return 0
    return sync_fahrlehrer_termine(fahrlehrer)


def _fsm_loesche_termine_task(fsm_ids: list[str]):
    """Hintergrund-Task zum Löschen mehrerer FSM-Termine anhand ihrer IDs."""
    client = FsmClient()
    for fid in fsm_ids:
        if fid:
            loesche_fsm_termin_by_id(fid, client=client)


def async_buche_sperrzeit_in_fsm(sperre: Sperrzeit):
    """Startet den FSM-Export einer Sperrzeit asynchron über django-q."""
    if not is_fsm_aktiv_fuer_fahrlehrer(sperre.fahrlehrer):
        return None
    if getattr(settings, "IM_TESTLAUF", False):
        return exportiere_sperrzeit_nach_fsm(sperre)
    try:
        from django_q.tasks import async_task

        return async_task("termine.services.fsm_sync._fsm_sync_sperrzeit_task", sperre.pk)
    except Exception:
        logger.exception("Fehler beim Einreihen der FSM-Sperrzeit-Aufgabe – führe synchron aus")
        return exportiere_sperrzeit_nach_fsm(sperre)


def async_buche_in_fsm(buchung: Buchung):
    """Startet FSM-Buchungsexport asynchron über django-q."""
    if not is_fsm_aktiv_fuer_fahrlehrer(buchung.termin.fahrlehrer):
        return None
    if getattr(settings, "IM_TESTLAUF", False):
        return buche_in_fsm(buchung)
    try:
        from django_q.tasks import async_task

        return async_task(
            "termine.services.fsm_sync._fsm_sync_termin_task", buchung.termin.pk
        )
    except Exception:
        logger.exception("Fehler beim Einreihen der FSM-Buchungs-Aufgabe – führe synchron aus")
        return buche_in_fsm(buchung)


def async_storniere_in_fsm(buchung: Buchung):
    """Startet FSM-Storno asynchron über django-q."""
    if not is_fsm_aktiv_fuer_fahrlehrer(buchung.termin.fahrlehrer):
        return None
    if getattr(settings, "IM_TESTLAUF", False):
        return storniere_in_fsm(buchung)
    try:
        from django_q.tasks import async_task

        return async_task("termine.services.fsm_sync._fsm_storno_task", buchung.termin.pk)
    except Exception:
        logger.exception("Fehler beim Einreihen der FSM-Storno-Aufgabe – führe synchron aus")
        return storniere_in_fsm(buchung)


def async_sync_fahrlehrer_termine(fahrlehrer: Fahrlehrer):
    """Startet den FSM-Terminabgleich eines Fahrlehrers asynchron."""
    if not is_fsm_aktiv_fuer_fahrlehrer(fahrlehrer):
        return None
    if getattr(settings, "IM_TESTLAUF", False):
        return sync_fahrlehrer_termine(fahrlehrer)
    try:
        from django_q.tasks import async_task

        return async_task(
            "termine.services.fsm_sync._fsm_sync_fahrlehrer_task", fahrlehrer.pk
        )
    except Exception:
        logger.exception("Fehler beim Einreihen der FSM-Sync-Aufgabe – führe synchron aus")
        return sync_fahrlehrer_termine(fahrlehrer)


def async_loesche_fsm_termine(fsm_ids: list[str]):
    """Startet das Löschen von FSM-Termin-IDs asynchron."""
    valid_ids = [fid for fid in fsm_ids if fid]
    if not valid_ids or not getattr(settings, "FSM_SYNC_ENABLED", False):
        return None
    if getattr(settings, "IM_TESTLAUF", False):
        client = FsmClient()
        for fid in valid_ids:
            loesche_fsm_termin_by_id(fid, client=client)
        return True
    try:
        from django_q.tasks import async_task

        return async_task("termine.services.fsm_sync._fsm_loesche_termine_task", valid_ids)
    except Exception:
        logger.exception("Fehler beim Einreihen der FSM-Lösch-Aufgabe – führe synchron aus")
        client = FsmClient()
        for fid in valid_ids:
            loesche_fsm_termin_by_id(fid, client=client)
        return True
