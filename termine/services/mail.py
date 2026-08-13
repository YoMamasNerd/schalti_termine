"""Versand aller Buchungs-E-Mails.

Fehler beim Mailversand dürfen eine Buchung nie zerstören: Wenn der SMTP-Server
klemmt, wird geloggt, aber die Buchung bleibt gültig.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone

from .ics import buchung_als_ics

logger = logging.getLogger(__name__)


def _kontext(buchung, **extra) -> dict:
    termin = buchung.termin
    return {
        "buchung": buchung,
        "termin": termin,
        "terminart": termin.terminart,
        "fahrlehrer": termin.fahrlehrer,
        "beginn": timezone.localtime(termin.beginn),
        "ende": timezone.localtime(termin.ende),
        "site_name": settings.SITE_NAME,
        "site_url": settings.SITE_BASE_URL,
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
    if not empfaenger:
        return False
    body = render_to_string(template, kontext)
    nachricht = EmailMessage(
        subject=betreff,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=empfaenger,
        reply_to=[antwort_an] if antwort_an else None,
    )
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
        template="mail/bestaetigung_anfordern.txt",
        empfaenger=[buchung.email],
        kontext=kontext,
        antwort_an=buchung.termin.fahrlehrer.email,
    )


def buchung_bestaetigt_kunde(buchung) -> bool:
    """Schritt 2: verbindliche Bestätigung mit Kalendereintrag im Anhang."""
    return _senden(
        betreff=f"Terminbestätigung: {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="mail/buchung_bestaetigt.txt",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung),
        antwort_an=buchung.termin.fahrlehrer.email,
    )


def buchung_bestaetigt_fahrlehrer(buchung) -> bool:
    """Benachrichtigung an den Fahrlehrer über eine neue bestätigte Buchung."""
    return _senden(
        betreff=f"Neue Buchung: {buchung.name} am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="mail/buchung_intern.txt",
        empfaenger=[buchung.termin.fahrlehrer.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung),
        antwort_an=buchung.email,
    )


def storno_kunde(buchung) -> bool:
    return _senden(
        betreff=f"Termin storniert: {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="mail/storno_kunde.txt",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung, storniert=True),
        ics_methode="CANCEL",
        antwort_an=buchung.termin.fahrlehrer.email,
    )


def storno_fahrlehrer(buchung) -> bool:
    return _senden(
        betreff=f"Storniert: {buchung.name} am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="mail/storno_intern.txt",
        empfaenger=[buchung.termin.fahrlehrer.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung, storniert=True),
        ics_methode="CANCEL",
    )


def erinnerung(buchung) -> bool:
    return _senden(
        betreff=f"Erinnerung: Ihr Beratungstermin am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="mail/erinnerung.txt",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung),
        antwort_an=buchung.termin.fahrlehrer.email,
    )
