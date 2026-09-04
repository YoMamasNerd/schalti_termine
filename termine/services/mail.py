"""Versand aller Buchungs-E-Mails.

Fehler beim Mailversand dürfen eine Buchung nie zerstören: Wenn der SMTP-Server
klemmt, wird geloggt, aber die Buchung bleibt gültig.

Jede Mail geht zweigestaltig raus: erst der Text, dann dieselbe Nachricht als
HTML im Bild der Webseite. Postfächer, die kein HTML anzeigen wollen oder
dürfen, nehmen den Text – deshalb bleibt er vollwertig und ist keine Notiz mit
Verweis auf „siehe HTML-Fassung“.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .ics import buchung_als_ics

logger = logging.getLogger(__name__)


def get_from_email() -> str:
    """Liefert die konfigurierte Absenderadresse aus den FahrschulEinstellungen."""
    try:
        from ..models import FahrschulEinstellungen

        einst = FahrschulEinstellungen.get_solo()
        if einst.email_from:
            return einst.email_from
        if einst.email_user:
            return einst.email_user
    except Exception:
        pass
    return getattr(settings, "DEFAULT_FROM_EMAIL", "mail@fahrschule-schaltwerk.de")


def get_kontakt_email() -> str:
    """Liefert die reine Kontakt-E-Mail-Adresse ohne Klammern/Namen."""
    kontakt_email = get_from_email()
    if "<" in kontakt_email and ">" in kontakt_email:
        return kontakt_email.split("<")[1].replace(">", "").strip()
    return kontakt_email.strip()


def get_mail_connection(fail_silently: bool = False):
    """Erzeugt eine Django-Mail-Connection basierend auf den FahrschulEinstellungen."""
    try:
        from ..models import FahrschulEinstellungen

        einst = FahrschulEinstellungen.get_solo()
        cfg = einst.get_effective_email_config()
    except Exception:
        return get_connection(fail_silently=fail_silently)

    # Wenn in der DB ein Host hinterlegt ist, nutzen wir SMTP mit diesen Werten:
    if cfg.get("host"):
        return get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=cfg["host"],
            port=cfg["port"],
            username=cfg["user"] or None,
            password=cfg["password"] or None,
            use_tls=cfg["use_tls"],
            use_ssl=cfg["use_ssl"],
            timeout=10,
            fail_silently=fail_silently,
        )

    # Andernfalls Standard-Backend (z. B. locmem im Testlauf oder console)
    return get_connection(fail_silently=fail_silently)


def _kontext(buchung, **extra) -> dict:
    termin = buchung.termin
    kontakt_email = get_kontakt_email()

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
        "impressum_url": f"{settings.SITE_BASE_URL}{reverse('termine:impressum')}",
        "datenschutz_url": f"{settings.SITE_BASE_URL}{reverse('termine:datenschutz')}",
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
    from_email = get_from_email()
    connection = get_mail_connection(fail_silently=False)

    nachricht = EmailMultiAlternatives(
        subject=betreff,
        body=render_to_string(f"mail/{template}.txt", kontext),
        from_email=from_email,
        to=empfaenger,
        reply_to=[antwort_an] if antwort_an else None,
        connection=connection,
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


def _sende_mail_task(funktions_name: str, buchung_id: int, **kwargs) -> bool:
    """Wird im Worker-Prozess (django-q) ausgeführt."""
    from ..models import Buchung

    try:
        buchung = (
            Buchung.objects.select_related("termin", "termin__fahrlehrer", "termin__terminart")
            .get(pk=buchung_id)
        )
    except Buchung.DoesNotExist:
        logger.warning("Buchung %s für Mail-Task nicht mehr vorhanden", buchung_id)
        return False

    func = _MAIL_FUNKTIONEN_DIREKT.get(funktions_name)
    if func:
        return func(buchung, **kwargs)
    return False


def _im_hintergrund_oder_direkt(funktions_name: str, buchung, **kwargs) -> bool:
    if getattr(settings, "IM_TESTLAUF", False):
        func = _MAIL_FUNKTIONEN_DIREKT.get(funktions_name)
        return func(buchung, **kwargs) if func else False

    try:
        from django_q.tasks import async_task

        async_task(
            "termine.services.mail._sende_mail_task",
            funktions_name,
            buchung.pk,
            **kwargs,
        )
        return True
    except Exception:
        logger.exception(
            "Fehler beim Einreihen der Mail-Aufgabe (%s) in die Queue – führe synchron aus",
            funktions_name,
        )
        func = _MAIL_FUNKTIONEN_DIREKT.get(funktions_name)
        return func(buchung, **kwargs) if func else False


def _direkt_bestaetigung_anfordern(buchung) -> bool:
    """Schritt 1 des Double-Opt-in: Link zum Bestätigen der Buchung."""
    kontext = _kontext(buchung, minuten=settings.RESERVATION_MINUTES)
    return _senden(
        betreff=f"Bitte bestätigen: Ihr Beratungstermin am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="bestaetigung_anfordern",
        empfaenger=[buchung.email],
        kontext=kontext,
        antwort_an=None,
    )


def _direkt_buchung_bestaetigt_kunde(buchung) -> bool:
    """Schritt 2: verbindliche Bestätigung mit Kalendereintrag im Anhang."""
    return _senden(
        betreff=f"Terminbestätigung: {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="buchung_bestaetigt",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung),
        antwort_an=None,
    )


def _direkt_buchung_bestaetigt_fahrlehrer(buchung) -> bool:
    """Benachrichtigung an den Fahrlehrer über eine neue bestätigte Buchung."""
    return _senden(
        betreff=f"Neue Buchung: {buchung.name} am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="buchung_intern",
        empfaenger=[buchung.termin.fahrlehrer.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung),
        antwort_an=buchung.email,
    )


def _direkt_storno_kunde(buchung) -> bool:
    return _senden(
        betreff=f"Termin storniert: {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="storno_kunde",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung, storniert=True),
        ics_methode="CANCEL",
        antwort_an=None,
    )


def _direkt_storno_fahrlehrer(buchung) -> bool:
    return _senden(
        betreff=f"Storniert: {buchung.name} am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="storno_intern",
        empfaenger=[buchung.termin.fahrlehrer.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung, storniert=True),
        ics_methode="CANCEL",
    )


def _direkt_erinnerung(buchung) -> bool:
    return _senden(
        betreff=f"Erinnerung: Ihr Beratungstermin am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="erinnerung",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung),
        ics=buchung_als_ics(buchung),
        antwort_an=None,
    )


def _direkt_reservierung_verfallen(buchung) -> bool:
    """Der Kunde hat den Bestätigungslink nicht geklickt – Reservierung verfallen."""
    return _senden(
        betreff=f"Reservierung abgelaufen: {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="reservierung_verfallen",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung),
        antwort_an=None,
    )


def _direkt_buchung_verschoben_kunde(buchung, alter_beginn: dt.datetime | str) -> bool:
    """Benachrichtigung an den Kunden über einen verschobenen Termin."""
    if isinstance(alter_beginn, str):
        alter_beginn = timezone.datetime.fromisoformat(alter_beginn)
    return _senden(
        betreff=f"Termin verschoben: Neuer Termin am {timezone.localtime(buchung.termin.beginn):%d.%m.%Y um %H:%M} Uhr",
        template="buchung_verschoben",
        empfaenger=[buchung.email],
        kontext=_kontext(buchung, alter_beginn=alter_beginn),
        ics=buchung_als_ics(buchung),
        antwort_an=None,
    )


_MAIL_FUNKTIONEN_DIREKT = {
    "bestaetigung_anfordern": _direkt_bestaetigung_anfordern,
    "buchung_bestaetigt_kunde": _direkt_buchung_bestaetigt_kunde,
    "buchung_bestaetigt_fahrlehrer": _direkt_buchung_bestaetigt_fahrlehrer,
    "storno_kunde": _direkt_storno_kunde,
    "storno_fahrlehrer": _direkt_storno_fahrlehrer,
    "erinnerung": _direkt_erinnerung,
    "reservierung_verfallen": _direkt_reservierung_verfallen,
    "buchung_verschoben_kunde": _direkt_buchung_verschoben_kunde,
}


def bestaetigung_anfordern(buchung) -> bool:
    return _im_hintergrund_oder_direkt("bestaetigung_anfordern", buchung)


def buchung_bestaetigt_kunde(buchung) -> bool:
    return _im_hintergrund_oder_direkt("buchung_bestaetigt_kunde", buchung)


def buchung_bestaetigt_fahrlehrer(buchung) -> bool:
    return _im_hintergrund_oder_direkt("buchung_bestaetigt_fahrlehrer", buchung)


def storno_kunde(buchung) -> bool:
    return _im_hintergrund_oder_direkt("storno_kunde", buchung)


def storno_fahrlehrer(buchung) -> bool:
    return _im_hintergrund_oder_direkt("storno_fahrlehrer", buchung)


def erinnerung(buchung) -> bool:
    return _im_hintergrund_oder_direkt("erinnerung", buchung)


def reservierung_verfallen(buchung) -> bool:
    return _im_hintergrund_oder_direkt("reservierung_verfallen", buchung)


def buchung_verschoben_kunde(buchung, alter_beginn: dt.datetime) -> bool:
    alter_beginn_str = (
        alter_beginn.isoformat() if isinstance(alter_beginn, dt.datetime) else str(alter_beginn)
    )
    return _im_hintergrund_oder_direkt(
        "buchung_verschoben_kunde", buchung, alter_beginn=alter_beginn_str
    )
