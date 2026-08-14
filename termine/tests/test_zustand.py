"""Tests für die Zustandsprüfung von Webserver und Worker.

Diese Prüfungen sind selbst die Alarmanlage. Geht eine davon still kaputt,
merkt niemand mehr, dass der Worker steht – deshalb sind hier auch die
Fehlerfälle abgedeckt, nicht nur der Gutfall.
"""

from __future__ import annotations

import datetime as dt
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django_q.models import Schedule

from termine.services import zustand


class Datenbankpruefung(TestCase):
    def test_erreichbare_datenbank(self):
        befund = zustand.datenbank()
        self.assertTrue(befund.gesund)

    def test_ausgefallene_datenbank(self):
        with mock.patch.object(
            zustand.connection, "cursor", side_effect=DatabaseError("weg")
        ):
            befund = zustand.datenbank()
        self.assertFalse(befund.gesund)
        self.assertIn("DatabaseError", befund.meldung)


def job_anlegen(faellig_vor_minuten: int = 0) -> Schedule:
    """Ein Job, dessen Ausführung so viele Minuten zurückliegt."""
    return Schedule.objects.create(
        name="Reservierungen aufräumen",
        func="termine.jobs.reservierungen_aufraeumen",
        schedule_type=Schedule.MINUTES,
        minutes=5,
        repeats=-1,
        next_run=timezone.now() - dt.timedelta(minutes=faellig_vor_minuten),
    )


class Fahrplanpruefung(TestCase):
    def test_ohne_eingerichtete_jobs_ist_etwas_faul(self):
        befund = zustand.jobs()
        self.assertFalse(befund.gesund)
        self.assertIn("jobs_einrichten", befund.meldung)

    def test_puenktlicher_fahrplan_ist_gesund(self):
        job_anlegen()
        self.assertTrue(zustand.jobs().gesund)

    def test_kurzer_rueckstand_ist_noch_kein_alarm(self):
        # Der Worker holt sich den Job im Minutentakt; ein paar Minuten
        # Rückstand sind normaler Betrieb und kein Ausfall.
        job_anlegen(faellig_vor_minuten=5)
        self.assertTrue(zustand.jobs().gesund)

    def test_langer_rueckstand_meldet_den_stehenden_worker(self):
        job_anlegen(faellig_vor_minuten=45)
        befund = zustand.jobs()
        self.assertFalse(befund.gesund)
        self.assertIn("Reservierungen aufräumen", befund.meldung)
        self.assertIn("qcluster", befund.meldung)

    def test_datenbankfehler_gilt_als_krank(self):
        # Anders als bei der Zustandsseite ist ein stiller Fehlschlag hier
        # nicht harmlos: Wer den Fahrplan nicht lesen kann, weiß nicht, ob
        # die Jobs laufen – und muss das melden, nicht verschweigen.
        with mock.patch.object(
            Schedule.objects, "count", side_effect=DatabaseError("weg")
        ):
            befund = zustand.jobs()
        self.assertFalse(befund.gesund)
        self.assertIn("Fahrplan nicht lesbar", befund.meldung)

    def test_toleranz_ist_einstellbar(self):
        job_anlegen(faellig_vor_minuten=20)
        self.assertFalse(zustand.jobs().gesund)
        self.assertTrue(zustand.jobs(toleranz_minuten=60).gesund)


class Kommando(TestCase):
    def test_gesunder_fahrplan_endet_ohne_fehler(self):
        job_anlegen()
        call_command("jobs_pruefen", stdout=StringIO(), stderr=StringIO())

    def test_stehender_worker_endet_mit_rueckgabewert_eins(self):
        job_anlegen(faellig_vor_minuten=45)
        with self.assertRaises(SystemExit) as ende:
            call_command("jobs_pruefen", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(ende.exception.code, 1)

    def test_toleranz_laesst_sich_uebergeben(self):
        job_anlegen(faellig_vor_minuten=20)
        call_command("jobs_pruefen", "--toleranz", "60", stdout=StringIO(), stderr=StringIO())


class Zustandsseite(TestCase):
    def test_ohne_login_erreichbar_und_knapp(self):
        antwort = self.client.get(reverse("termine:healthz"))
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.content, b"ok\n")
        self.assertEqual(antwort["Cache-Control"], "no-store")

    def test_ohne_datenbank_meldet_sie_503(self):
        with mock.patch.object(
            zustand.connection, "cursor", side_effect=DatabaseError("weg")
        ):
            with self.assertLogs("termine.views", level="WARNING") as protokoll:
                antwort = self.client.get(reverse("termine:healthz"))
        self.assertEqual(antwort.status_code, 503)
        self.assertIn("Zustandsprüfung fehlgeschlagen", "\n".join(protokoll.output))

    def test_verraet_nach_aussen_keine_einzelheiten(self):
        # Die Seite ist öffentlich. Woran es hakt, gehört ins Log, nicht in
        # die Antwort.
        with mock.patch.object(
            zustand.connection, "cursor", side_effect=DatabaseError("weg")
        ):
            with self.assertLogs("termine.views", level="WARNING"):
                antwort = self.client.get(reverse("termine:healthz"))
        self.assertEqual(antwort.content, b"fehler\n")
        self.assertNotIn(b"DatabaseError", antwort.content)
