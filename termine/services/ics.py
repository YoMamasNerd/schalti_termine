"""iCalendar-Erzeugung: Anhang für Bestätigungsmails und Abo-Feed."""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone
from icalendar import Calendar, Event, vText

PRODID = "-//Schalti Termine//Fahrschule Beratungstermine//DE"


def _domain() -> str:
    host = urlparse(settings.SITE_BASE_URL).hostname or "schalti-termine.local"
    return host


def _uid(buchung) -> str:
    return f"{buchung.referenz}@{_domain()}"


def _titel(buchung) -> str:
    return f"{buchung.termin.terminart.name} – {buchung.name}"


def _beschreibung(buchung) -> str:
    zeilen = [
        f"Terminart: {buchung.termin.terminart.name}",
        f"Name: {buchung.name}",
        f"E-Mail: {buchung.email}",
    ]
    if buchung.telefon:
        zeilen.append(f"Telefon: {buchung.telefon}")
    if buchung.fuehrerscheinklasse:
        zeilen.append(f"Führerscheinklasse: {buchung.get_fuehrerscheinklasse_display()}")
    if buchung.nachricht:
        zeilen.append("")
        zeilen.extend(buchung.nachricht.splitlines())
    zeilen.append("")
    zeilen.append(f"Termin verwalten: {buchung.verwaltungs_url}")
    return "\n".join(zeilen)


def _kalender() -> Calendar:
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    return cal


def _event(buchung, *, sequence: int = 0, storniert: bool = False) -> Event:
    termin = buchung.termin
    kontakt_email = getattr(settings, "DEFAULT_FROM_EMAIL", "mail@fahrschule-schaltwerk.de")
    if "<" in kontakt_email and ">" in kontakt_email:
        kontakt_email = kontakt_email.split("<")[1].replace(">", "").strip()

    event = Event()
    event.add("uid", _uid(buchung))
    event.add("dtstamp", timezone.now())
    event.add("dtstart", termin.beginn)
    event.add("dtend", termin.ende)
    event.add("summary", _titel(buchung))
    event.add("description", _beschreibung(buchung))
    event.add("sequence", sequence)
    event.add("status", "CANCELLED" if storniert else "CONFIRMED")
    event.add("transp", "OPAQUE")
    if termin.terminart.ort:
        event.add("location", vText(termin.terminart.ort))
    event.add("organizer", f"MAILTO:{kontakt_email}")
    event.add("url", buchung.verwaltungs_url)
    return event


def buchung_als_ics(buchung, *, storniert: bool = False) -> bytes:
    """Einzelner Kalendereintrag – als Anhang für die Mail an den Kunden."""
    cal = _kalender()
    cal.add("method", "CANCEL" if storniert else "PUBLISH")
    cal.add_component(_event(buchung, sequence=1 if storniert else 0, storniert=storniert))
    return cal.to_ical()


def fahrlehrer_feed(fahrlehrer, *, tage_rueckblick: int = 30) -> bytes:
    """Abo-Feed mit allen bestätigten Terminen eines Fahrlehrers.

    Wird von Outlook, Google Kalender oder Apple Kalender periodisch abgerufen,
    daher enthält er auch die jüngste Vergangenheit.
    """
    from ..models import Buchung

    cal = _kalender()
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", f"Beratungstermine {fahrlehrer.name}")
    cal.add("x-wr-timezone", settings.TIME_ZONE)
    cal.add("refresh-interval;value=duration", "PT1H")

    grenze = timezone.now() - dt.timedelta(days=tage_rueckblick)
    buchungen = (
        Buchung.objects.filter(
            termin__fahrlehrer=fahrlehrer,
            termin__beginn__gte=grenze,
            status=Buchung.Status.BESTAETIGT,
        )
        .select_related("termin", "termin__terminart", "termin__fahrlehrer")
        .order_by("termin__beginn")
    )
    for buchung in buchungen:
        cal.add_component(_event(buchung))
    return cal.to_ical()
