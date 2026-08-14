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
from . import mail

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

    def _mails():
        mail.buchung_bestaetigt_kunde(buchung)
        mail.buchung_bestaetigt_fahrlehrer(buchung)

    transaction.on_commit(_mails)
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

    if benachrichtigen and war_bestaetigt:
        def _mails():
            # Der Kunde wird immer benachrichtigt. Der Fahrlehrer nur dann,
            # wenn die Absage vom Kunden kam – hat die Fahrschule selbst
            # abgesagt, weiß sie es bereits.
            mail.storno_kunde(buchung)
            if von != "fahrschule":
                mail.storno_fahrlehrer(buchung)

        transaction.on_commit(_mails)

    logger.info("Buchung %s storniert (%s)", buchung.referenz, von)
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
            Termin.objects.filter(
                pk=aktuell.termin_id, status=Termin.Status.RESERVIERT
            ).update(status=Termin.Status.FREI)
            anzahl += 1

    if anzahl:
        logger.info("%s abgelaufene Reservierungen freigegeben", anzahl)
    return anzahl


def erinnerungen_versenden(stunden_vorher: int | None = None) -> int:
    """Verschickt Erinnerungsmails für bestätigte Termine kurz vor dem Termin."""
    stunden_vorher = stunden_vorher or settings.REMINDER_HOURS_BEFORE
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
    anzahl = betroffen.update(
        name="Gelöscht",
        email="",
        telefon="",
        nachricht="",
        anonymisiert_am=timezone.now(),
    )
    if anzahl:
        logger.info("%s Buchungen anonymisiert (älter als %s Tage)", anzahl, tage)
    return anzahl
