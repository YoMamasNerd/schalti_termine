import datetime as dt

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from termine.models import Fahrlehrer, RhythmusRegel, Terminart
from termine.services.planung import generiere_termine


class Command(BaseCommand):
    help = "Legt eine spielbereite Beispiel-Fahrschule an (nur für Test und Demo)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--admin-passwort",
            default="admin",
            help="Passwort für den Demo-Benutzer „admin“ (Standard: admin).",
        )

    @transaction.atomic
    def handle(self, *args, **optionen):
        User = get_user_model()
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@example.org", optionen["admin_passwort"])
            self.stdout.write("Superuser „admin“ angelegt.")

        beratung, _ = Terminart.objects.get_or_create(
            slug="erstberatung",
            defaults={
                "name": "Erstberatung",
                "dauer_minuten": 30,
                "puffer_minuten": 5,
                "ort": "Fahrschule, Hauptstraße 1",
                "beschreibung": "Wir klären Ablauf, Kosten und Termine für Ihren Führerschein. "
                "Bringen Sie bitte einen Ausweis mit.",
            },
        )
        intensiv, _ = Terminart.objects.get_or_create(
            slug="beratung-intensiv",
            defaults={
                "name": "Ausführliche Beratung",
                "dauer_minuten": 60,
                "ort": "Fahrschule, Hauptstraße 1",
                "reihenfolge": 1,
            },
        )

        anna, _ = Fahrlehrer.objects.get_or_create(
            slug="anna-berger",
            defaults={
                "name": "Anna Berger",
                "email": "anna@example.org",
                "telefon": "0761 1234567",
                "bundesland": "BW",
                "horizont_wochen": 4,
                "vorlauf_stunden": 24,
                "beschreibung": "Klassen B, BE und A. Beratung gerne auch abends.",
            },
        )
        tom, _ = Fahrlehrer.objects.get_or_create(
            slug="tom-keller",
            defaults={
                "name": "Tom Keller",
                "email": "tom@example.org",
                "bundesland": "BY",
                "horizont_wochen": 6,
                "vorlauf_stunden": 48,
                "reihenfolge": 1,
                "beschreibung": "Schwerpunkt LKW und Anhänger (C, CE, BE).",
            },
        )

        heute = dt.date.today()
        RhythmusRegel.objects.get_or_create(
            fahrlehrer=anna,
            terminart=beratung,
            bezeichnung="Anna – Wochenmitte nachmittags",
            defaults={
                "wochentage": [1, 3],  # Dienstag, Donnerstag
                "beginn": dt.time(14, 0),
                "ende": dt.time(18, 0),
                "intervall_wochen": 1,
                "gueltig_ab": heute,
                "feiertage_auslassen": True,
            },
        )
        RhythmusRegel.objects.get_or_create(
            fahrlehrer=anna,
            terminart=intensiv,
            bezeichnung="Anna – Samstag, jede zweite Woche",
            defaults={
                "wochentage": [5],  # Samstag
                "beginn": dt.time(9, 0),
                "ende": dt.time(12, 0),
                "intervall_wochen": 2,
                "referenzwoche": heute,
                "gueltig_ab": heute,
                "feiertage_auslassen": True,
            },
        )
        RhythmusRegel.objects.get_or_create(
            fahrlehrer=tom,
            terminart=beratung,
            bezeichnung="Tom – Montag und Freitag vormittags",
            defaults={
                "wochentage": [0, 4],
                "beginn": dt.time(9, 0),
                "ende": dt.time(11, 30),
                "gueltig_ab": heute,
                "feiertage_auslassen": True,
            },
        )

        for fahrlehrer in (anna, tom):
            bericht = generiere_termine(fahrlehrer)
            self.stdout.write(f"{fahrlehrer.name}: {bericht.als_text()}")

        self.stdout.write(
            self.style.SUCCESS(
                "Beispieldaten angelegt. Login: admin / "
                f"{optionen['admin_passwort']} unter /intern/anmelden/"
            )
        )
