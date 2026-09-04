"""Erzeugt aus Rhythmus-Regeln konkrete Termine und pflegt sie fort.

Leitplanken, die der Generator immer einhält:

* Er fasst **nur die Zukunft** an. Alles vor „jetzt“ bleibt unverändert.
* Er löscht **niemals** reservierte oder gebuchte Termine.
* Er löscht **niemals** von Hand angelegte Termine.
* Er ist **idempotent**: zweimal laufen lassen ändert nichts.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import Buchung, Fahrlehrer, RhythmusRegel, Sperrzeit, Termin, Terminart
from .feiertage import feiertage_im_zeitraum

logger = logging.getLogger(__name__)


def lokal(tag: dt.date, uhrzeit: dt.time) -> dt.datetime:
    """Kombiniert Datum und lokale Uhrzeit zu einem zeitzonenbewussten Zeitpunkt."""
    return timezone.make_aware(dt.datetime.combine(tag, uhrzeit))


@dataclass
class Bericht:
    """Was ein Generatorlauf getan hat – für Log, Admin-Meldung und Tests."""

    fahrlehrer: str = ""
    von: dt.date | None = None
    bis: dt.date | None = None
    erstellt: int = 0
    entfernt: int = 0
    unveraendert: int = 0
    wiederbelebt: int = 0
    feiertage_uebersprungen: int = 0
    sperrzeiten_uebersprungen: int = 0
    konflikte_uebersprungen: int = 0
    feiertage: dict[dt.date, str] = field(default_factory=dict)

    def __add__(self, other: "Bericht") -> "Bericht":
        summe = Bericht(von=self.von, bis=self.bis)
        summe.erstellt = self.erstellt + other.erstellt
        summe.entfernt = self.entfernt + other.entfernt
        summe.unveraendert = self.unveraendert + other.unveraendert
        summe.wiederbelebt = self.wiederbelebt + other.wiederbelebt
        summe.feiertage_uebersprungen = (
            self.feiertage_uebersprungen + other.feiertage_uebersprungen
        )
        summe.sperrzeiten_uebersprungen = (
            self.sperrzeiten_uebersprungen + other.sperrzeiten_uebersprungen
        )
        summe.konflikte_uebersprungen = (
            self.konflikte_uebersprungen + other.konflikte_uebersprungen
        )
        summe.feiertage = {**self.feiertage, **other.feiertage}
        return summe

    def als_text(self) -> str:
        teile = [
            f"{self.erstellt} erstellt",
            f"{self.entfernt} entfernt",
            f"{self.unveraendert} unverändert",
        ]
        if self.wiederbelebt:
            teile.append(f"{self.wiederbelebt} wieder angeboten")
        if self.feiertage_uebersprungen:
            teile.append(f"{self.feiertage_uebersprungen} wegen Feiertag ausgelassen")
        if self.sperrzeiten_uebersprungen:
            teile.append(f"{self.sperrzeiten_uebersprungen} wegen Sperrzeit ausgelassen")
        if self.konflikte_uebersprungen:
            teile.append(f"{self.konflikte_uebersprungen} wegen Überschneidung ausgelassen")
        return ", ".join(teile)


def slots_einer_regel(regel: RhythmusRegel, tag: dt.date) -> list[tuple[dt.datetime, dt.datetime]]:
    """Zerlegt das Zeitfenster einer Regel an einem Tag in einzelne Termine.

    Es wird nur ein Termin erzeugt, wenn er komplett in das Fenster passt – ein
    angebrochener Rest am Ende fällt weg.
    """
    if not regel.terminart:
        return []
    art = regel.terminart
    dauer = dt.timedelta(minutes=art.dauer_minuten)
    schritt = dt.timedelta(minutes=art.schrittweite_minuten)

    fenster_beginn = lokal(tag, regel.beginn)
    fenster_ende = lokal(tag, regel.ende)

    slots: list[tuple[dt.datetime, dt.datetime]] = []
    cursor = fenster_beginn
    while cursor + dauer <= fenster_ende:
        slots.append((cursor, cursor + dauer))
        cursor += schritt
    return slots


def generiere_regel_sperren(
    fahrlehrer: Fahrlehrer, von: dt.date, bis: dt.date, jetzt: dt.datetime
) -> list[int]:
    """Erzeugt für alle aktiven Sperrzeit-Regeln konkrete Sperrzeiten im Horizont."""
    sperr_regeln = list(
        RhythmusRegel.objects.filter(
            fahrlehrer=fahrlehrer,
            aktiv=True,
            regel_art=RhythmusRegel.RegelArt.SPERRE,
        )
    )
    if not sperr_regeln:
        return []

    feiertage = feiertage_im_zeitraum(fahrlehrer.bundesland, von, bis)
    erzeugte_pks: list[int] = []

    tag = von
    while tag <= bis:
        ist_feiertag = tag in feiertage
        for regel in sperr_regeln:
            if not regel.gilt_am(tag):
                continue
            if ist_feiertag and regel.feiertage_auslassen:
                continue

            sperr_start = lokal(tag, regel.beginn)
            sperr_ende = lokal(tag, regel.ende)

            if sperr_ende <= jetzt:
                continue

            sperre, created = Sperrzeit.objects.update_or_create(
                fahrlehrer=fahrlehrer,
                regel=regel,
                beginn=sperr_start,
                defaults={
                    "ende": sperr_ende,
                    "grund": regel.grund or regel.bezeichnung or "Regel-Blocker",
                    "typ": regel.sperrzeit_typ or Sperrzeit.Typ.PRIVAT,
                    "herkunft": Sperrzeit.Herkunft.MANUELL,
                },
            )
            erzeugte_pks.append(sperre.pk)

            if created:
                # Freie Termine im gesperrten Zeitraum entfernen
                termine_entfernen(
                    Termin.objects.filter(
                        fahrlehrer=fahrlehrer,
                        status=Termin.Status.FREI,
                        beginn__lt=sperr_ende,
                        ende__gt=sperr_start,
                    )
                )

                if getattr(settings, "FSM_SYNC_ENABLED", False) and fahrlehrer.fsm_sync_aktiv and fahrlehrer.fsm_id:
                    from . import fsm_sync

                    transaction.on_commit(lambda s=sperre: fsm_sync.async_buche_sperrzeit_in_fsm(s))

        tag += dt.timedelta(days=1)

    return erzeugte_pks


def _ueberschneidet(a_beginn, a_ende, b_beginn, b_ende) -> bool:
    return a_beginn < b_ende and b_beginn < a_ende


def gewuenschte_termine(
    fahrlehrer: Fahrlehrer, von: dt.date, bis: dt.date, bericht: Bericht
) -> dict[dt.datetime, tuple[dt.datetime, Terminart, RhythmusRegel]]:
    """Der Soll-Zustand: welche Termine die Angebots-Regeln in diesem Zeitraum ergeben."""
    regeln = list(
        RhythmusRegel.objects.filter(
            fahrlehrer=fahrlehrer,
            aktiv=True,
            regel_art=RhythmusRegel.RegelArt.ANGEBOT,
        ).select_related("terminart")
    )
    if not regeln:
        return {}

    feiertage = feiertage_im_zeitraum(fahrlehrer.bundesland, von, bis)
    bericht.feiertage = feiertage

    roh: list[tuple[dt.datetime, dt.datetime, Terminart, RhythmusRegel]] = []
    tag = von
    while tag <= bis:
        ist_feiertag = tag in feiertage
        for regel in regeln:
            if not regel.gilt_am(tag):
                continue
            if ist_feiertag and regel.feiertage_auslassen:
                bericht.feiertage_uebersprungen += len(slots_einer_regel(regel, tag))
                continue
            for beginn, ende in slots_einer_regel(regel, tag):
                roh.append((beginn, ende, regel.terminart, regel))
        tag += dt.timedelta(days=1)

    # Sperrzeiten des Fahrlehrers im Zeitraum vorab laden.
    fenster_start = lokal(von, dt.time.min)
    fenster_ende = lokal(bis, dt.time.max)
    sperren = list(
        Sperrzeit.objects.filter(
            fahrlehrer=fahrlehrer, beginn__lt=fenster_ende, ende__gt=fenster_start
        ).values_list("beginn", "ende")
    )

    # Überschneidungen zwischen mehreren Regeln auflösen: Wer früher beginnt,
    # gewinnt. So kann eine zweite Regel dasselbe Fenster nicht doppelt belegen.
    roh.sort(key=lambda eintrag: (eintrag[0], eintrag[1]))
    soll: dict[dt.datetime, tuple[dt.datetime, Terminart, RhythmusRegel]] = {}
    letztes_ende: dt.datetime | None = None
    for beginn, ende, art, regel in roh:
        if letztes_ende is not None and beginn < letztes_ende:
            bericht.konflikte_uebersprungen += 1
            continue
        if any(_ueberschneidet(beginn, ende, s_beginn, s_ende) for s_beginn, s_ende in sperren):
            bericht.sperrzeiten_uebersprungen += 1
            continue
        soll[beginn] = (ende, art, regel)
        letztes_ende = ende
    return soll


def termine_entfernen(termine) -> tuple[int, set[int]]:
    """Nimmt Termine aus dem Angebot.

    Gibt zurück, wie viele gelöscht wurden, und die Schlüssel derer, die als
    ENTFALLEN stehen geblieben sind.

    Termine ohne Buchungshistorie werden gelöscht. Termine mit Historie
    bleiben als ENTFALLEN stehen – auch eine stornierte Buchung ist der Beleg
    dafür, dass hier jemand gebucht hatte, und `Buchung.termin` steht bewusst
    auf PROTECT, damit ein Aufräumlauf so einen Beleg nicht wegwirft.

    Ohne diese Unterscheidung endete jeder Löschpfad in einem ProtectedError,
    sobald ein Termin einmal gebucht und wieder storniert worden war – und
    genau das ist der Normalfall einer Terminbuchung mit Selbst-Storno.
    """
    pks = list(termine.values_list("pk", flat=True))
    if not pks:
        return 0, set()

    # Eventuell in FSM hinterlegte Termine ebenfalls dort löschen
    fsm_ids_to_delete = list(
        termine.exclude(fsm_termin_id="").values_list("fsm_termin_id", flat=True)
    )
    if fsm_ids_to_delete:
        from . import fsm_sync

        transaction.on_commit(lambda: fsm_sync.async_loesche_fsm_termine(fsm_ids_to_delete))

    mit_historie = set(
        Buchung.objects.filter(termin_id__in=pks).values_list("termin_id", flat=True)
    )
    ohne_historie = [pk for pk in pks if pk not in mit_historie]

    if mit_historie:
        Termin.objects.filter(pk__in=mit_historie).update(status=Termin.Status.ENTFALLEN)
    geloescht = 0
    if ohne_historie:
        geloescht, _ = Termin.objects.filter(pk__in=ohne_historie).delete()
    return geloescht, mit_historie


@transaction.atomic
def generiere_termine(
    fahrlehrer: Fahrlehrer,
    *,
    wochen: int | None = None,
    ab: dt.date | None = None,
    aufraeumen: bool = True,
) -> Bericht:
    """Rollt die Rhythmus-Regeln eines Fahrlehrers in konkrete Termine aus."""
    from ..models import FahrschulEinstellungen

    wochen = wochen or FahrschulEinstellungen.get_solo().horizont_wochen or settings.DEFAULT_HORIZON_WEEKS
    jetzt = timezone.now()
    von = ab or timezone.localdate(jetzt)
    bis = von + dt.timedelta(days=wochen * 7 - 1)

    bericht = Bericht(fahrlehrer=fahrlehrer.name, von=von, bis=bis)

    # Der Generator arbeitet ausschließlich in der Zukunft.
    fenster_start = max(lokal(von, dt.time.min), jetzt)
    fenster_ende = lokal(bis, dt.time.max)

    # 0. Sperrzeiten aus Blocker-Regeln erzeugen & veraltete aufräumen
    erzeugte_sperren = generiere_regel_sperren(fahrlehrer, von, bis, jetzt)
    if aufraeumen:
        Sperrzeit.objects.filter(
            fahrlehrer=fahrlehrer,
            regel__isnull=False,
            beginn__gte=fenster_start,
            beginn__lte=fenster_ende,
        ).exclude(pk__in=erzeugte_sperren).delete()

    soll = gewuenschte_termine(fahrlehrer, von, bis, bericht)
    soll = {beginn: wert for beginn, wert in soll.items() if beginn >= fenster_start}

    bestand = list(
        Termin.objects.select_for_update()
        .filter(fahrlehrer=fahrlehrer, beginn__gte=fenster_start, beginn__lte=fenster_ende)
        .order_by("beginn")
    )
    bestand_nach_beginn = {termin.beginn: termin for termin in bestand}

    # 1. Aufräumen: freie Regel-Termine, die es laut Regeln nicht mehr geben soll.
    if aufraeumen:
        zu_loeschen = [
            termin.pk
            for termin in bestand
            if termin.herkunft == Termin.Herkunft.REGEL
            and termin.status == Termin.Status.FREI
            and termin.beginn not in soll
        ]
        if zu_loeschen:
            geloescht, entfallen = termine_entfernen(
                Termin.objects.filter(pk__in=zu_loeschen)
            )
            bericht.entfernt = geloescht + len(entfallen)
            # Die entfallenen bleiben im Bestand: Sie belegen ihre Uhrzeit
            # weiterhin, und der nächste Lauf kann sie wiederbeleben.
            bestand = [t for t in bestand if t.pk in entfallen or t.pk not in set(zu_loeschen)]
            for termin in bestand:
                if termin.pk in entfallen:
                    termin.status = Termin.Status.ENTFALLEN
            bestand_nach_beginn = {t.beginn: t for t in bestand}

    # 2. Anlegen, was fehlt – ohne bestehende Termine zu überlappen.
    belegt = [(t.beginn, t.ende) for t in bestand]
    neue: list[Termin] = []
    for beginn in sorted(soll):
        ende, art, regel = soll[beginn]
        vorhanden = bestand_nach_beginn.get(beginn)
        if vorhanden is not None:
            if vorhanden.status == Termin.Status.ENTFALLEN:
                # Die Regel deckt diesen Zeitpunkt wieder ab. Ein zweiter
                # Termin zur selben Uhrzeit ginge ohnehin nicht – der
                # Unique-Index verbietet ihn –, also wird dieser wieder
                # angeboten.
                Termin.objects.filter(pk=vorhanden.pk).update(
                    status=Termin.Status.FREI, terminart=art, regel=regel, ende=ende
                )
                bericht.wiederbelebt += 1
            else:
                bericht.unveraendert += 1
            continue
        if any(_ueberschneidet(beginn, ende, b_beginn, b_ende) for b_beginn, b_ende in belegt):
            bericht.konflikte_uebersprungen += 1
            continue
        neue.append(
            Termin(
                fahrlehrer=fahrlehrer,
                terminart=art,
                beginn=beginn,
                ende=ende,
                status=Termin.Status.FREI,
                herkunft=Termin.Herkunft.REGEL,
                regel=regel,
            )
        )
        belegt.append((beginn, ende))

    if neue:
        # ignore_conflicts schützt gegen parallele Läufe (Unique-Constraint auf
        # fahrlehrer + beginn), ohne den ganzen Lauf abzubrechen. Genau deshalb
        # ist len(neue) nicht die Wahrheit: Ein paralleler Lauf kann einzelne
        # Zeilen still verworfen haben. Deshalb wird nachgezählt.
        im_fenster = Termin.objects.filter(
            fahrlehrer=fahrlehrer, beginn__gte=fenster_start, beginn__lte=fenster_ende
        )
        vorher = len(bestand)
        Termin.objects.bulk_create(neue, ignore_conflicts=True)
        bericht.erstellt = max(im_fenster.count() - vorher, 0)

        if getattr(settings, "FSM_SYNC_ENABLED", False) and fahrlehrer.fsm_sync_aktiv and fahrlehrer.fsm_id:
            from . import fsm_sync

            transaction.on_commit(lambda: fsm_sync.async_sync_fahrlehrer_termine(fahrlehrer))

    logger.info("Terminplanung %s (%s bis %s): %s", fahrlehrer, von, bis, bericht.als_text())
    return bericht


def generiere_alle(*, wochen: int | None = None, ab: dt.date | None = None) -> Bericht:
    """Läuft über alle aktiven Fahrlehrer."""
    gesamt = Bericht(fahrlehrer="alle")
    for fahrlehrer in Fahrlehrer.objects.filter(aktiv=True):
        gesamt = gesamt + generiere_termine(fahrlehrer, wochen=wochen, ab=ab)
    return gesamt


def vorschau(
    fahrlehrer: Fahrlehrer, *, wochen: int | None = None, ab: dt.date | None = None
) -> tuple[dict[dt.datetime, tuple], Bericht]:
    """Wie `generiere_termine`, aber ohne zu schreiben – für die Regel-Vorschau."""
    from ..models import FahrschulEinstellungen

    wochen = wochen or FahrschulEinstellungen.get_solo().horizont_wochen or settings.DEFAULT_HORIZON_WEEKS
    von = ab or timezone.localdate()
    bis = von + dt.timedelta(days=wochen * 7 - 1)
    bericht = Bericht(fahrlehrer=fahrlehrer.name, von=von, bis=bis)
    return gewuenschte_termine(fahrlehrer, von, bis, bericht), bericht


@transaction.atomic
def termine_manuell_anlegen(
    fahrlehrer: Fahrlehrer,
    terminart: Terminart,
    tag: dt.date,
    von: dt.time,
    bis: dt.time,
    *,
    notiz: str = "",
) -> tuple[list[Termin], int]:
    """Legt für einen einzelnen Tag von Hand eine Terminreihe an.

    Das ist die Funktion hinter der Tagesplanung: „Am 17.09. von 14:00 bis 17:00
    Beratungen anbieten“. Gibt die neuen Termine und die Zahl der wegen
    Überschneidung ausgelassenen zurück.
    """
    dauer = dt.timedelta(minutes=terminart.dauer_minuten)
    schritt = dt.timedelta(minutes=terminart.schrittweite_minuten)

    fenster_beginn = lokal(tag, von)
    fenster_ende = lokal(tag, bis)

    # „Nur die Zukunft" gilt auch für die Handplanung: Wer heute um 14 Uhr ein
    # Fenster ab 9 Uhr einträgt, meint den Rest des Tages und nicht den Vormittag.
    jetzt = timezone.now()
    vergangen = 0
    if fenster_beginn < jetzt:
        fenster_beginn = jetzt

    belegt = list(
        Termin.objects.select_for_update()
        .filter(fahrlehrer=fahrlehrer, beginn__lt=fenster_ende, ende__gt=fenster_beginn)
        .values_list("beginn", "ende")
    )
    # Auch von Hand dürfen keine Slots in Sperrzeiten entstehen.
    sperren = list(
        Sperrzeit.objects.filter(
            fahrlehrer=fahrlehrer, beginn__lt=fenster_ende, ende__gt=fenster_beginn
        ).values_list("beginn", "ende")
    )

    neue: list[Termin] = []
    uebersprungen = 0
    # Das Raster bleibt am ursprünglichen Fensterbeginn hängen, damit „ab 9 Uhr"
    # nicht zu krummen Uhrzeiten wie 14:07 führt.
    cursor = lokal(tag, von)
    while cursor < jetzt and cursor + dauer <= fenster_ende:
        cursor += schritt
        vergangen += 1
    while cursor + dauer <= fenster_ende:
        beginn, ende = cursor, cursor + dauer
        if any(_ueberschneidet(beginn, ende, b, e) for b, e in belegt + sperren):
            uebersprungen += 1
        else:
            neue.append(
                Termin(
                    fahrlehrer=fahrlehrer,
                    terminart=terminart,
                    beginn=beginn,
                    ende=ende,
                    status=Termin.Status.FREI,
                    herkunft=Termin.Herkunft.MANUELL,
                    notiz=notiz,
                )
            )
            belegt.append((beginn, ende))
        cursor += schritt

    if neue:
        Termin.objects.bulk_create(neue, ignore_conflicts=True)
        if getattr(settings, "FSM_SYNC_ENABLED", False) and fahrlehrer.fsm_sync_aktiv and fahrlehrer.fsm_id:
            from . import fsm_sync

            transaction.on_commit(lambda: fsm_sync.async_sync_fahrlehrer_termine(fahrlehrer))

    return neue, uebersprungen + vergangen


@dataclass
class RhythmusKollision:
    fahrlehrer: Fahrlehrer
    tag: dt.date
    beginn: dt.datetime
    ende: dt.datetime
    terminart: Terminart
    regel: RhythmusRegel
    grund: str
    alternative_fahrlehrer: list[Fahrlehrer] = field(default_factory=list)


def finde_kollisionen_rhythmus_regeln(
    fahrlehrer_liste,
    *,
    von: dt.date | None = None,
    bis: dt.date | None = None,
) -> list[RhythmusKollision]:
    """Findet alle Zeitfenster von aktiven Rhythmus-Regeln, die wegen Sperrzeiten

    oder FSM-Blockern nicht als Beratungstermin angeboten werden können.
    """
    from ..models import FahrschulEinstellungen

    jetzt = timezone.now()
    von = von or timezone.localdate(jetzt)
    if bis is None:
        wochen = (
            FahrschulEinstellungen.get_solo().horizont_wochen
            or settings.DEFAULT_HORIZON_WEEKS
        )
        bis = von + dt.timedelta(days=wochen * 7 - 1)

    fenster_start = max(lokal(von, dt.time.min), jetzt)
    fenster_ende = lokal(bis, dt.time.max)

    alle_aktiven_fl = list(Fahrlehrer.objects.filter(aktiv=True))

    # Sperrzeiten aller aktiven Fahrlehrer im Zeitraum laden
    alle_sperren = list(
        Sperrzeit.objects.filter(
            fahrlehrer__in=alle_aktiven_fl,
            beginn__lt=fenster_ende,
            ende__gt=fenster_start,
        ).select_related("fahrlehrer")
    )
    # Termine aller aktiven Fahrlehrer im Zeitraum laden (um Alternativen zu prüfen)
    alle_termine = list(
        Termin.objects.filter(
            fahrlehrer__in=alle_aktiven_fl,
            beginn__lt=fenster_ende,
            ende__gt=fenster_start,
            status__in=[Termin.Status.FREI, Termin.Status.RESERVIERT, Termin.Status.GEBUCHT],
        ).select_related("fahrlehrer")
    )

    kollisionen: list[RhythmusKollision] = []

    for fl in fahrlehrer_liste:
        # Nur Angebots-Regeln: Eine Sperr-Regel *soll* den Kalender blockieren.
        # Trüge sie versehentlich eine Terminart, meldete sie sich sonst als
        # Kollision mit genau der Sperrzeit, die sie selbst erzeugt hat.
        regeln = list(
            RhythmusRegel.objects.filter(
                fahrlehrer=fl, aktiv=True, regel_art=RhythmusRegel.RegelArt.ANGEBOT
            ).select_related("terminart")
        )
        if not regeln:
            continue

        feiertage = feiertage_im_zeitraum(fl.bundesland, von, bis)
        fl_sperren = [s for s in alle_sperren if s.fahrlehrer_id == fl.pk]

        tag = von
        while tag <= bis:
            ist_feiertag = tag in feiertage
            for regel in regeln:
                if not regel.gilt_am(tag):
                    continue
                if ist_feiertag and regel.feiertage_auslassen:
                    continue

                for slot_beginn, slot_ende in slots_einer_regel(regel, tag):
                    if slot_beginn < jetzt:
                        continue

                    # Prüfen, ob eine Sperrzeit überlappt
                    kollidierende_sperre = None
                    for s in fl_sperren:
                        if _ueberschneidet(slot_beginn, slot_ende, s.beginn, s.ende):
                            kollidierende_sperre = s
                            break

                    if kollidierende_sperre:
                        # Alternative Fahrlehrer suchen: Wer hat zu dieser Zeit weder Sperrzeit noch Termin?
                        alternativen = []
                        for anderer_fl in alle_aktiven_fl:
                            if anderer_fl.pk == fl.pk:
                                continue
                            hat_sperre = any(
                                s.fahrlehrer_id == anderer_fl.pk
                                and _ueberschneidet(slot_beginn, slot_ende, s.beginn, s.ende)
                                for s in alle_sperren
                            )
                            hat_termin = any(
                                t.fahrlehrer_id == anderer_fl.pk
                                and _ueberschneidet(slot_beginn, slot_ende, t.beginn, t.ende)
                                for t in alle_termine
                            )
                            if not hat_sperre and not hat_termin:
                                alternativen.append(anderer_fl)

                        grund = kollidierende_sperre.grund or "Blockiert"
                        kollisionen.append(
                            RhythmusKollision(
                                fahrlehrer=fl,
                                tag=tag,
                                beginn=slot_beginn,
                                ende=slot_ende,
                                terminart=regel.terminart,
                                regel=regel,
                                grund=grund,
                                alternative_fahrlehrer=alternativen,
                            )
                        )
            tag += dt.timedelta(days=1)

    kollisionen.sort(key=lambda k: k.beginn)
    return kollisionen
