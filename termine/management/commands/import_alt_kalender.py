"""Importiert anstehende Beratungstermine aus dem bisherigen Nextcloud-Kalender."""

from __future__ import annotations

import base64
import datetime as dt
import re
import urllib.request
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

import icalendar

from termine.models import Buchung, Fahrlehrer, Sperrzeit, Termin, Terminart

DEFAULT_CALDAV_URL = (
    "https://fahrschule-schaltwerk.de/termine/remote.php/dav/calendars/dermaib/termine-fahrschule-beratung/?export"
)
DEFAULT_CALDAV_USER = "dermaib"


class Command(BaseCommand):
    help = "Importiert anstehende Beratungstermine aus dem bisherigen Nextcloud/CalDAV-Kalender."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            default=DEFAULT_CALDAV_URL,
            help="CalDAV-Export-URL des Nextcloud-Kalenders.",
        )
        parser.add_argument(
            "--user",
            default=DEFAULT_CALDAV_USER,
            help="Nextcloud-Benutzername.",
        )
        parser.add_argument(
            "--password",
            required=True,
            help="Passwort oder App-Token für den CalDAV-Zugriff.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur anzeigen, was importiert werden würde, ohne die Datenbank zu verändern.",
        )
        parser.add_argument(
            "--remove-blockers",
            action="store_true",
            default=True,
            help="Entfernt bestehende FSM-Sperrzeiten mit 'Beratungen' im kollidierenden Zeitraum (Standard: True).",
        )

    def handle(self, *args, **options):
        url = options["url"]
        user = options["user"]
        password = options["password"]
        dry_run = options["dry_run"]
        remove_blockers = options["remove_blockers"]

        self.stdout.write(f"Lade Kalendereinträge von {url} ...")

        req = urllib.request.Request(url)
        auth_bytes = f"{user}:{password}".encode("utf-8")
        auth_header = base64.b64encode(auth_bytes).decode("ascii")
        req.add_header("Authorization", f"Basic {auth_header}")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                cal_data = resp.read()
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Fehler beim Abrufen des Kalenders: {exc}"))
            return

        cal = icalendar.Calendar.from_ical(cal_data)
        berlin_tz = ZoneInfo("Europe/Berlin")
        jetzt = dt.datetime.now(berlin_tz)

        # Terminart ermitteln
        terminart = Terminart.objects.filter(aktiv=True).first()
        if not terminart:
            terminart = Terminart.objects.create(name="Beratungsgespräch", dauer_minuten=30)

        # Alle Fahrlehrer laden
        fahrlehrer_map = {fl.slug: fl for fl in Fahrlehrer.objects.all()}
        alle_fahrlehrer = list(Fahrlehrer.objects.filter(aktiv=True))

        if not alle_fahrlehrer:
            self.stderr.write(self.style.ERROR("Keine aktiven Fahrlehrer in der Datenbank vorhanden."))
            return

        stefan = fahrlehrer_map.get("stefan-richter") or alle_fahrlehrer[0]
        marten = fahrlehrer_map.get("marten") or alle_fahrlehrer[0]

        gefundene_termine = []

        for component in cal.walk():
            if component.name != "VEVENT":
                continue

            dtstart = component.get("dtstart")
            if not dtstart:
                continue

            st = dtstart.dt
            if isinstance(st, dt.date) and not isinstance(st, dt.datetime):
                st = dt.datetime.combine(st, dt.time.min, tzinfo=berlin_tz)
            elif hasattr(st, "tzinfo") and st.tzinfo is None:
                st = st.replace(tzinfo=berlin_tz)

            if st < jetzt:
                continue  # Vergangene Termine ignorieren

            summary = str(component.get("summary", "")).strip()
            # Ignoriere freie / ungebuchte Slots
            if summary.lower() in ["verfügbar", "available", "frei", ""]:
                continue

            dtend = component.get("dtend")
            end = dtend.dt if dtend else st + dt.timedelta(minutes=30)
            if hasattr(end, "tzinfo") and end.tzinfo is None:
                end = end.replace(tzinfo=berlin_tz)

            desc = str(component.get("description", "")).strip()
            loc = str(component.get("location", "")).strip()
            uid = str(component.get("uid", "")).strip()

            # Name aus Summary bereinigen (Haken und Status-Emojis entfernen)
            kunde_name = re.sub(r"^[✔️⌛\s\-\*]+", "", summary).strip()
            kunde_email = ""
            kunde_telefon = ""
            kunde_nachricht = ""

            # Description parsen: "Name | Telefon | E-Mail | Nachricht"
            if "|" in desc:
                teile = [t.strip() for t in desc.split("|")]
                if len(teile) >= 1 and teile[0]:
                    kunde_name = teile[0]
                if len(teile) >= 2 and teile[1]:
                    kunde_telefon = teile[1]
                if len(teile) >= 3 and teile[2]:
                    kunde_email = teile[2]
                if len(teile) >= 4 and teile[3]:
                    kunde_nachricht = teile[3]
            else:
                # E-Mail mit Regex suchen
                email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", desc)
                if email_match:
                    kunde_email = email_match.group(0)
                # Telefon mit Regex suchen
                tel_match = re.search(r"(\+?[0-9\s\-/]{7,25})", desc)
                if tel_match:
                    kunde_telefon = tel_match.group(0).strip()

            # Fahrlehrer-Zuordnung:
            # Prüfe, welcher Fahrlehrer an diesem Tag eine Beratungssperre / Zeitfenster hat
            zugeordneter_lehrer = None
            sperre_lehrer = Sperrzeit.objects.filter(
                beginn__lte=end,
                ende__gte=st,
                grund__icontains="beratung",
            ).select_related("fahrlehrer").first()

            if sperre_lehrer:
                zugeordneter_lehrer = sperre_lehrer.fahrlehrer
            else:
                # Standard-Zuordnung nach Monat (August -> Stefan, September -> Marten)
                if st.month == 8:
                    zugeordneter_lehrer = stefan
                else:
                    zugeordneter_lehrer = marten

            gefundene_termine.append({
                "start": st,
                "end": end,
                "kunde_name": kunde_name,
                "kunde_email": kunde_email or "keine-email@example.org",
                "kunde_telefon": kunde_telefon,
                "kunde_nachricht": kunde_nachricht,
                "fahrlehrer": zugeordneter_lehrer,
                "uid": uid,
                "raw_summary": summary,
            })

        gefundene_termine.sort(key=lambda x: x["start"])

        self.stdout.write(f"\n{len(gefundene_termine)} anstehende Kundentermine gefunden:")
        self.stdout.write("-" * 80)

        for i, item in enumerate(gefundene_termine, 1):
            st_lokal = item["start"].astimezone(berlin_tz)
            end_lokal = item["end"].astimezone(berlin_tz)
            self.stdout.write(
                f"[{i:2d}] {st_lokal:%d.%m.%Y %H:%M} – {end_lokal:%H:%M} | "
                f"Kunde: {item['kunde_name']:<22} | "
                f"FL: {item['fahrlehrer'].name:<14} | "
                f"Tel: {item['kunde_telefon'] or '–'} | "
                f"Mail: {item['kunde_email']}"
            )

        self.stdout.write("-" * 80)

        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY RUN] Es wurden keine Änderungen an der Datenbank vorgenommen."))
            return

        # Echten Import durchführen
        importiert_count = 0
        blocker_entfernt_count = 0

        with transaction.atomic():
            for item in gefundene_termine:
                fl = item["fahrlehrer"]
                st = item["start"]
                end = item["end"]

                # 1. Ggf. kollidierende FSM: Beratungen Sperrzeiten entfernen
                if remove_blockers:
                    kollidierende_sperren = Sperrzeit.objects.filter(
                        fahrlehrer=fl,
                        beginn__lte=end,
                        ende__gte=st,
                        grund__icontains="beratung",
                    )
                    anz = kollidierende_sperren.count()
                    if anz > 0:
                        kollidierende_sperren.delete()
                        blocker_entfernt_count += anz

                # 2. Termin anlegen oder aktualisieren
                termin, created = Termin.objects.update_or_create(
                    fahrlehrer=fl,
                    beginn=st,
                    defaults={
                        "ende": end,
                        "terminart": terminart,
                        "status": Termin.Status.GEBUCHT,
                        "herkunft": Termin.Herkunft.MANUELL,
                    },
                )

                # 3. Buchung anlegen oder aktualisieren
                buchung = Buchung.objects.filter(termin=termin, status__in=[Buchung.Status.OFFEN, Buchung.Status.BESTAETIGT]).first()
                if not buchung:
                    buchung = Buchung.objects.create(
                        termin=termin,
                        name=item["kunde_name"],
                        email=item["kunde_email"],
                        telefon=item["kunde_telefon"],
                        nachricht=item["kunde_nachricht"],
                        status=Buchung.Status.BESTAETIGT,
                        bestaetigt_am=timezone.now(),
                        einwilligung_am=timezone.now(),
                    )
                else:
                    buchung.name = item["kunde_name"]
                    buchung.email = item["kunde_email"]
                    buchung.telefon = item["kunde_telefon"]
                    buchung.nachricht = item["kunde_nachricht"]
                    buchung.status = Buchung.Status.BESTAETIGT
                    buchung.bestaetigt_am = buchung.bestaetigt_am or timezone.now()
                    buchung.save()

                importiert_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nErfolg: {importiert_count} Termine & Buchungen erfolgreich importiert! "
                f"({blocker_entfernt_count} kollidierende FSM-Beratungssperren aufgelöst)."
            )
        )
