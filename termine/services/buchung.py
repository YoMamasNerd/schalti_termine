"""Der Buchungsablauf mit Double-Opt-in.

Zustandsfolge einer Buchung::

    [Formular]  --reservieren-->  OFFEN      (Termin: RESERVIERT)
                --bestaetigen-->  BESTÄTIGT  (Termin: GEBUCHT)
                --stornieren-->   STORNIERT  (Termin: wieder FREI)
                --Zeitablauf-->   VERFALLEN  (Termin: wieder FREI)

Gegen Doppelbuchungen wirken drei Ebenen: eine Sperre auf der Termin-Zeile,
die Statusprüfung innerhalb derselben Transaktion und ein partieller
Unique-Index in der Datenbank (`nur_eine_aktive_buchung_pro_termin`).
"""

from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import Buchung, Termin
from . import fsm_sync, mail

logger = logging.getLogger(__name__)


class BuchungsFehler(Exception):
    """Die gewünschte Aktion ist im aktuellen Zustand nicht möglich."""


class TerminNichtVerfuegbar(BuchungsFehler):
    pass


@transaction.atomic
def reservieren(
    termin_id: int,
    *,
    name: str,
    email: str,
    telefon: str = "",
    fuehrerscheinklasse: str = "",
    nachricht: str = "",
) -> Buchung:
    """Reserviert einen freien Termin und schickt die Bestätigungsanfrage.

    Wirft `TerminNichtVerfuegbar`, wenn der Termin zwischenzeitlich weg ist –
    das ist der normale Ausgang eines Wettlaufs zweier Interessenten.
    """
    try:
        termin = (
            Termin.objects.select_for_update()
            .select_related("fahrlehrer", "terminart")
            .get(pk=termin_id)
        )
    except Termin.DoesNotExist as exc:
        raise TerminNichtVerfuegbar("Dieser Termin existiert nicht mehr.") from exc

    if termin.status != Termin.Status.FREI:
        raise TerminNichtVerfuegbar("Dieser Termin ist inzwischen vergeben.")
    if termin.beginn < termin.fahrlehrer.fruehester_start():
        raise TerminNichtVerfuegbar("Dieser Termin liegt zu kurzfristig und ist nicht mehr buchbar.")
    if termin.beginn > termin.fahrlehrer.spaetester_start():
        raise TerminNichtVerfuegbar(
            "Dieser Termin liegt weiter in der Zukunft, als zurzeit gebucht werden kann."
        )
    if termin.ist_gesperrt():
        raise TerminNichtVerfuegbar("Dieser Termin steht nicht mehr zur Verfügung.")

    jetzt = timezone.now()
    try:
        buchung = Buchung.objects.create(
            termin=termin,
            name=name.strip(),
            email=email.strip(),
            telefon=telefon.strip(),
            fuehrerscheinklasse=fuehrerscheinklasse,
            nachricht=nachricht.strip(),
            status=Buchung.Status.OFFEN,
            reserviert_bis=jetzt + dt.timedelta(minutes=settings.RESERVATION_MINUTES),
            einwilligung_am=jetzt,
        )
    except IntegrityError as exc:
        raise TerminNichtVerfuegbar("Dieser Termin ist inzwischen vergeben.") from exc

    termin.status = Termin.Status.RESERVIERT
    termin.save(update_fields=["status", "geaendert_am"])

    transaction.on_commit(lambda: mail.bestaetigung_anfordern(buchung))
    # Bewusst die Referenz statt der E-Mail-Adresse: Nach DATA_RETENTION_DAYS
    # putzt die Anonymisierung die Datenbank – im Log stünde die Adresse sonst
    # weiter, und zwar für immer.
    logger.info("Termin %s reserviert (%s)", termin.pk, buchung.referenz)
    return buchung


@transaction.atomic
def bestaetigen(buchung: Buchung) -> Buchung:
    """Schließt das Double-Opt-in ab und macht die Buchung verbindlich."""
    buchung = Buchung.objects.select_for_update().select_related("termin").get(pk=buchung.pk)

    if buchung.status == Buchung.Status.BESTAETIGT:
        return buchung  # Doppelklick auf den Link – kein Fehler.
    if buchung.status != Buchung.Status.OFFEN:
        raise BuchungsFehler("Diese Buchung kann nicht mehr bestätigt werden.")
    if buchung.ist_abgelaufen:
        raise BuchungsFehler(
            "Die Reservierung ist abgelaufen. Bitte buchen Sie den Termin erneut."
        )

    buchung.status = Buchung.Status.BESTAETIGT
    buchung.bestaetigt_am = timezone.now()
    buchung.reserviert_bis = None
    buchung.save(update_fields=["status", "bestaetigt_am", "reserviert_bis"])

    Termin.objects.filter(pk=buchung.termin_id).update(status=Termin.Status.GEBUCHT)
    buchung.termin.status = Termin.Status.GEBUCHT

    def _nach_bestaetigung():
        mail.buchung_bestaetigt_kunde(buchung)
        mail.buchung_bestaetigt_fahrlehrer(buchung)
        fsm_sync.async_buche_in_fsm(buchung)

    transaction.on_commit(_nach_bestaetigung)
    logger.info("Buchung %s bestätigt", buchung.referenz)
    return buchung


@transaction.atomic
def stornieren(buchung: Buchung, *, von: str = "kunde", benachrichtigen: bool = True) -> Buchung:
    """Storniert eine Buchung und gibt den Termin wieder frei."""
    buchung = Buchung.objects.select_for_update().select_related("termin").get(pk=buchung.pk)

    if buchung.status in (Buchung.Status.STORNIERT, Buchung.Status.VERFALLEN):
        return buchung

    war_bestaetigt = buchung.status == Buchung.Status.BESTAETIGT
    buchung.status = Buchung.Status.STORNIERT
    buchung.storniert_am = timezone.now()
    buchung.storniert_von = von
    buchung.reserviert_bis = None
    buchung.save(update_fields=["status", "storniert_am", "storniert_von", "reserviert_bis"])

    # Der Termin wird nur dann wieder angeboten, wenn er noch in der Zukunft liegt.
    if buchung.termin.beginn > timezone.now():
        Termin.objects.filter(pk=buchung.termin_id).update(status=Termin.Status.FREI)
        buchung.termin.status = Termin.Status.FREI

    if war_bestaetigt:
        def _nach_storno():
            if benachrichtigen:
                mail.storno_kunde(buchung)
                if von != "fahrschule":
                    mail.storno_fahrlehrer(buchung)
            fsm_sync.async_storniere_in_fsm(buchung)

        transaction.on_commit(_nach_storno)

    logger.info("Buchung %s storniert (%s)", buchung.referenz, von)
    return buchung


@transaction.atomic
def verschieben(
    buchung: Buchung,
    neuer_termin_id: int,
    *,
    benachrichtigen: bool = True,
) -> Buchung:
    """Verschiebt eine bestehende Buchung auf einen anderen freien Termin."""
    buchung = (
        Buchung.objects.select_for_update()
        .select_related("termin", "termin__fahrlehrer", "termin__terminart")
        .get(pk=buchung.pk)
    )

    if not buchung.ist_aktiv:
        raise BuchungsFehler("Nur aktive Buchungen können verschoben werden.")

    alter_termin = buchung.termin
    alter_beginn = timezone.localtime(alter_termin.beginn)

    try:
        neuer_termin = (
            Termin.objects.select_for_update()
            .select_related("fahrlehrer", "terminart")
            .get(pk=neuer_termin_id)
        )
    except Termin.DoesNotExist as exc:
        raise TerminNichtVerfuegbar("Der gewählte Ziel-Termin existiert nicht.") from exc

    if neuer_termin.pk == alter_termin.pk:
        return buchung

    if neuer_termin.status != Termin.Status.FREI:
        raise TerminNichtVerfuegbar("Der gewählte Ziel-Termin ist nicht mehr frei.")
    # Beim Verschieben durch die Fahrschule gelten die Grenzen der öffentlichen
    # Buchungsseite nicht: Der Ziel-Termin darf auch beim Kollegen liegen
    # (Krankheitsvertretung) und kurzfristiger als der Mindest-Vorlauf sein.
    # Geprüft bleibt nur die Sperrzeit – niemand soll auf einen blockierten
    # Slot schieben, etwa auf eine Fahrstunde des Ziel-Fahrlehrers.
    if neuer_termin.ist_gesperrt():
        raise TerminNichtVerfuegbar("Der gewählte Ziel-Termin steht nicht zur Verfügung.")

    # Alter Termin wird wieder frei, falls er noch in der Zukunft liegt
    if alter_termin.beginn > timezone.now():
        Termin.objects.filter(pk=alter_termin.pk).update(status=Termin.Status.FREI)
        alter_termin.status = Termin.Status.FREI

    # Neuer Termin wird belegt
    neuer_status = (
        Termin.Status.GEBUCHT
        if buchung.status == Buchung.Status.BESTAETIGT
        else Termin.Status.RESERVIERT
    )
    Termin.objects.filter(pk=neuer_termin.pk).update(status=neuer_status)
    neuer_termin.status = neuer_status

    # Bei FSM-Sync: Altes FSM-Event stornieren und auf neuem Termin eintragen
    war_bestaetigt = buchung.status == Buchung.Status.BESTAETIGT
    if war_bestaetigt:
        fsm_sync.async_storniere_in_fsm(buchung)

    buchung.termin = neuer_termin
    buchung.save(update_fields=["termin"])

    if war_bestaetigt:
        def _nach_verschieben():
            if benachrichtigen:
                mail.buchung_verschoben_kunde(buchung, alter_beginn)
            fsm_sync.async_buche_in_fsm(buchung)

        transaction.on_commit(_nach_verschieben)

    logger.info(
        "Buchung %s verschoben von %s auf %s",
        buchung.referenz,
        alter_beginn,
        neuer_termin.beginn,
    )
    return buchung


def abgelaufene_reservierungen_freigeben() -> int:
    """Gibt Termine frei, deren Bestätigungslink nicht rechtzeitig geklickt wurde."""
    jetzt = timezone.now()
    abgelaufen = Buchung.objects.filter(
        status=Buchung.Status.OFFEN, reserviert_bis__lt=jetzt
    ).select_related("termin")

    anzahl = 0
    for buchung in abgelaufen:
        with transaction.atomic():
            aktuell = Buchung.objects.select_for_update().get(pk=buchung.pk)
            if aktuell.status != Buchung.Status.OFFEN or not aktuell.ist_abgelaufen:
                continue
            aktuell.status = Buchung.Status.VERFALLEN
            aktuell.reserviert_bis = None
            aktuell.save(update_fields=["status", "reserviert_bis"])
            # Nur zukünftige Termine dürfen wieder ins Angebot: Ein vergangener
            # Termin als FREI wäre ein buchbarer Slot in der Vergangenheit.
            Termin.objects.filter(
                pk=aktuell.termin_id,
                status=Termin.Status.RESERVIERT,
                beginn__gt=jetzt,
            ).update(status=Termin.Status.FREI)
            # Der Kunde soll wissen, dass seine Reservierung weg ist – sonst
            # wartet er vergeblich auf eine Bestätigung, die nie kommt.
            mail.reservierung_verfallen(aktuell)
            anzahl += 1

    if anzahl:
        logger.info("%s abgelaufene Reservierungen freigegeben", anzahl)
    return anzahl


def erinnerungen_versenden(stunden_vorher: int | None = None) -> int:
    """Verschickt Erinnerungsmails für bestätigte Termine kurz vor dem Termin."""
    if stunden_vorher is None:
        try:
            from ..models.einstellungen import FahrschulEinstellungen
            einst = FahrschulEinstellungen.get_solo()
            stunden_vorher = einst.erinnerung_stunden_vorher
        except Exception:
            stunden_vorher = getattr(settings, "REMINDER_HOURS_BEFORE", 24)

    if stunden_vorher is not None and stunden_vorher <= 0:
        return 0

    jetzt = timezone.now()
    faellig = Buchung.objects.filter(
        status=Buchung.Status.BESTAETIGT,
        erinnerung_am__isnull=True,
        termin__beginn__gt=jetzt,
        termin__beginn__lte=jetzt + dt.timedelta(hours=stunden_vorher),
    ).select_related("termin", "termin__terminart", "termin__fahrlehrer")

    anzahl = 0
    for buchung in faellig:
        if mail.erinnerung(buchung):
            Buchung.objects.filter(pk=buchung.pk).update(erinnerung_am=timezone.now())
            anzahl += 1

    if anzahl:
        logger.info("%s Erinnerungen versendet", anzahl)
    return anzahl


#: Was beim Anonymisieren überschrieben wird. Die Buchung selbst bleibt
#: bestehen, damit Termin, Dauer und Fahrlehrer für die Statistik erhalten
#: sind – ohne jeden Bezug zur Person.
ANONYM = {
    "name": "Gelöscht",
    "email": "",
    "telefon": "",
    "nachricht": "",
}


@transaction.atomic
def daten_loeschen(buchung: Buchung) -> Buchung:
    """Löscht die personenbezogenen Daten einer Buchung auf Wunsch des Kunden.

    Steht der Termin noch bevor und ist die Buchung aktiv, wird er zuvor
    storniert – sonst bliebe ein Termin belegt, zu dem niemand mehr zuzuordnen
    wäre. Die Absage geht dabei den gewohnten Weg samt Benachrichtigung: Der
    Kunde bekommt seine Stornobestätigung, der Fahrlehrer erfährt, dass sein
    Termin wieder frei ist. Danach erst wird überschrieben – die Mail braucht
    die Adresse ja noch.
    """
    buchung = Buchung.objects.select_for_update().select_related("termin").get(pk=buchung.pk)

    if buchung.ist_aktiv and buchung.termin.beginn > timezone.now():
        stornieren(buchung, von="kunde")

    Buchung.objects.filter(pk=buchung.pk).update(**ANONYM, anonymisiert_am=timezone.now())

    # Bewusst frisch geladen statt refresh_from_db(): Die Storno-Mails hängen
    # als on_commit-Rückruf an der Instanz, die `stornieren` sich geholt hat,
    # und die trägt die E-Mail-Adresse noch. Würde diese Instanz hier
    # überschrieben, ginge die Absagebestätigung an eine leere Adresse – also
    # gar nicht. Der Kunde bekäme nichts mehr zu sehen und nichts mehr zu
    # lesen.
    frisch = Buchung.objects.select_related("termin").get(pk=buchung.pk)
    logger.info("Buchung %s auf Kundenwunsch gelöscht", frisch.referenz)
    return frisch


def alte_buchungen_anonymisieren(tage: int | None = None) -> int:
    """Löscht personenbezogene Daten abgelaufener Buchungen (DSGVO-Löschkonzept).

    Die Buchung selbst bleibt bestehen, damit die Statistik stimmt – Name,
    E-Mail, Telefon und Nachricht werden aber unwiederbringlich überschrieben.
    """
    tage = tage or settings.DATA_RETENTION_DAYS
    grenze = timezone.now() - dt.timedelta(days=tage)
    betroffen = Buchung.objects.filter(
        anonymisiert_am__isnull=True, termin__beginn__lt=grenze
    )
    anzahl = betroffen.update(**ANONYM, anonymisiert_am=timezone.now())
    if anzahl:
        logger.info("%s Buchungen anonymisiert (älter als %s Tage)", anzahl, tage)
    return anzahl
