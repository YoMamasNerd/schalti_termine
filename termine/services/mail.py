"""Versand aller Buchungs-E-Mails.

Fehler beim Mailversand dürfen eine Buchung nie zerstören: Wenn der SMTP-Server
klemmt, wird geloggt, aber die Buchung bleibt gültig.

Jede Mail geht zweigestaltig raus: erst der Text, dann dieselbe Nachricht als
HTML im Bild der Webseite. Postfächer, die kein HTML anzeigen wollen oder
dürfen, nehmen den Text – deshalb bleibt er vollwertig und ist keine Notiz mit
Verweis auf „siehe HTML-Fassung“.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .ics import buchung_als_ics

logger = logging.getLogger(__name__)


def _kontext(buchung, **extra) -> dict:
    termin = buchung.termin
    kontakt_email = getattr(settings, "DEFAULT_FROM_EMAIL", "mail@fahrschule-schaltwerk.de")
    if "<" in kontakt_email and ">" in kontakt_email:
        kontakt_email = kontakt_email.split("<")[1].replace(">", "").strip()

    return {
        "buchung": buchung,
        "termin": termin,
        "terminart": termin.terminart,
        "fahrlehrer": termin.fahrlehrer,
        "kontakt_email": kontakt_email,
        "beginn": timezone.localtime(termin.beginn),
        "ende": timezone.localtime(termin.ende),
        "site_name": settings.SITE_NAME,
        "site_url": settings.SITE_BASE_URL,
        "buchungen_url": settings.SITE_BASE_URL + reverse("termine:buchungen"),
        # Mails entstehen ohne Request, also auch ohne Kontextprozessor: Die
        # rechtlichen Links für den Fuß müssen hier von Hand dazu.
        "impressum_url": (
            f"{settings.SITE_BASE_URL}{settings.IMPRESSUM_URL}"
            if settings.IMPRESSUM_URL.startswith("/")
            else settings.IMPRESSUM_URL
        ) if settings.IMPRESSUM_URL else "",
        "datenschutz_url": (
            f"{settings.SITE_BASE_URL}{settings.DATENSCHUTZ_URL}"
            if settings.DATENSCHUTZ_URL.startswith("/")
            else settings.DATENSCHUTZ_URL
        ) if settings.DATENSCHUTZ_URL else "",
        **extra,
    }


def _senden(
    *,
    betreff: str,
    template: str,
    empfaenger: list[str],
    kontext: dict,
    ics: bytes | None = None,
    ics_name: str = "termin.ics",
    ics_methode: str = "PUBLISH",
    antwort_an: str | None = None,
) -> bool:
    """`template` ist der Name ohne Endung; `.txt` und `.html` gehören zusammen."""
    if not empfaenger:
        return False
    nachricht = EmailMultiAlternatives(
        subject=betreff,
        body=render_to_string(f"mail/{template}.txt", kontext),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=empfaenger,
        reply_to=[antwort_an] if antwort_an else None,
    )
    nachricht.attach_alternative(render_to_string(f"mail/{template}.html", kontext), "text/html")
    if ics:
        nachricht.attach(ics_name, ics, f'text/calendar; charset=utf-8; method={ics_methode}')
    try:
        nachricht.send(fail_silently=False)
        return True
    except Exception:  # noqa: BLE001 – Versandfehler darf die Buchung nicht kippen
        logger.exception("E-Mail „%s“ an %s konnte nicht versendet werden", betreff, empfaenger)
        return False


def bestaetigung_anfordern(buchung) -> bool:
    """Schritt 1 des Double-Opt-in: Link zum Bestätigen der Buchung."""
    kontext = _kontext(buchung, minuten=settings.RESERVATION_MINUTES)
    return _senden(
        betreff=f"Bitte bestätigen: Ihr Beratungstermin am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="bestaetigung_anfordern",
        empfaenger=[buchung.email],
        kontext=kontext,
        antwort_an=None,
    )


def buchung_bestaetigt_kunde(buchung) -> bool:
    """Schritt 2: verbindliche Bestätigung mit Kalendereintrag im Anhang."""
    return _senden(
        betreff=f"Terminbestätigung: {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="buchung_bestaetigt",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung),
        antwort_an=None,
    )


def buchung_bestaetigt_fahrlehrer(buchung) -> bool:
    """Benachrichtigung an den Fahrlehrer über eine neue bestätigte Buchung."""
    return _senden(
        betreff=f"Neue Buchung: {buchung.name} am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="buchung_intern",
        empfaenger=[buchung.termin.fahrlehrer.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung),
        antwort_an=buchung.email,
    )


def storno_kunde(buchung) -> bool:
    return _senden(
        betreff=f"Termin storniert: {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="storno_kunde",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung, storniert=True),
        ics_methode="CANCEL",
        antwort_an=None,
    )


def storno_fahrlehrer(buchung) -> bool:
    return _senden(
        betreff=f"Storniert: {buchung.name} am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="storno_intern",
        empfaenger=[buchung.termin.fahrlehrer.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung, storniert=True),
        ics_methode="CANCEL",
    )


def erinnerung(buchung) -> bool:
    return _senden(
        betreff=f"Erinnerung: Ihr Beratungstermin am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="erinnerung",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung),
        antwort_an=None,
    )


def buchung_verschoben_kunde(buchung, alter_beginn: dt.datetime) -> bool:
    """Benachrichtigung an den Kunden über einen verschobenen Termin."""
    return _senden(
        betreff=f"Termin verschoben: Neuer Termin am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="buchung_verschoben",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung, alter_beginn=alter_beginn),
        ics=buchung_als_ics(buchung),
        antwort_an=None,
    )
