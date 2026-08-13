"""Tests für die Management-Kommandos.

Diese Kommandos laufen im Betrieb aus cron oder aus dem Container-Start heraus.
Ein Tippfehler in der Argumentbehandlung fällt dort erst auf, wenn die Planung
schon stehen geblieben ist.
"""

import datetime as dt
from io import StringIO

from django.core import mail as django_mail
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone
from django_q.models import Schedule

from termine.management.commands.jobs_einrichten import JOBS
from termine.models import Buchung, Fahrlehrer, RhythmusRegel, Termin, Terminart


class KommandoBasis(TestCase):
    def setUp(self):
        self.art = Terminart.objects.create(name="Beratung", dauer_minuten=30)
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )

    def regel(self, fahrlehrer=None):
        return RhythmusRegel.objects.create(
            fahrlehrer=fahrlehrer or self.fahrlehrer,
            terminart=self.art,
            wochentage=[0, 1, 2, 3, 4, 5, 6],
            beginn=dt.time(9, 0),
            ende=dt.time(11, 0),
            gueltig_ab=timezone.localdate(),
        )

    def rufe(self, *args, **kwargs):
        ausgabe = StringIO()
        call_command(*args, stdout=ausgabe, stderr=StringIO(), **kwargs)
        return ausgabe.getvalue()


class TermineGenerieren(KommandoBasis):
    def test_ohne_argumente_laufen_alle_fahrlehrer(self):
        self.regel()
        zweiter = Fahrlehrer.objects.create(
            name="Tom Keller", email="tom@example.org", bundesland="BE", vorlauf_stunden=0
        )
        self.regel(zweiter)

        ausgabe = self.rufe("termine_generieren")

        self.assertIn("erstellt", ausgabe)
        self.assertTrue(Termin.objects.filter(fahrlehrer=self.fahrlehrer).exists())
        self.assertTrue(Termin.objects.filter(fahrlehrer=zweiter).exists())

    def test_einzelner_fahrlehrer_per_kuerzel(self):
        self.regel()
        anderer = Fahrlehrer.objects.create(
            name="Tom Keller", email="tom@example.org", bundesland="BE", vorlauf_stunden=0
        )
        self.regel(anderer)

        self.rufe("termine_generieren", "--fahrlehrer", self.fahrlehrer.slug)

        self.assertTrue(Termin.objects.filter(fahrlehrer=self.fahrlehrer).exists())
        self.assertFalse(Termin.objects.filter(fahrlehrer=anderer).exists())

    def test_horizont_laesst_sich_ueberschreiben(self):
        self.regel()

        self.rufe("termine_generieren", "--wochen", "1")

        grenze = timezone.now() + dt.timedelta(days=8)
        self.assertFalse(Termin.objects.filter(beginn__gt=grenze).exists())
        self.assertTrue(Termin.objects.exists())

    def test_startdatum_laesst_sich_setzen(self):
        self.regel()
        ab = timezone.localdate() + dt.timedelta(days=14)

        self.rufe("termine_generieren", "--ab", ab.isoformat(), "--wochen", "1")

        frueheste = Termin.objects.order_by("beginn").first()
        self.assertIsNotNone(frueheste)
        self.assertGreaterEqual(timezone.localtime(frueheste.beginn).date(), ab)

    def test_unbekannter_fahrlehrer_bricht_ab(self):
        with self.assertRaises(CommandError):
            self.rufe("termine_generieren", "--fahrlehrer", "gibt-es-nicht")

    def test_kaputtes_datum_bricht_ab(self):
        with self.assertRaises(CommandError):
            self.rufe("termine_generieren", "--ab", "17.09.2026")


class BuchungenPflegen(KommandoBasis):
    def termin_mit_buchung(self, stunden_voraus, status, **extra):
        beginn = (timezone.now() + dt.timedelta(hours=stunden_voraus)).replace(
            minute=0, second=0, microsecond=0
        )
        termin = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )
        return termin, Buchung.objects.create(
            termin=termin, name="Max Muster", email="max@example.org", status=status, **extra
        )

    def test_erledigt_alle_drei_aufgaben(self):
        termin, abgelaufen = self.termin_mit_buchung(
            48,
            Buchung.Status.OFFEN,
            reserviert_bis=timezone.now() - dt.timedelta(minutes=1),
        )
        Termin.objects.filter(pk=termin.pk).update(status=Termin.Status.RESERVIERT)
        _, faellig = self.termin_mit_buchung(5, Buchung.Status.BESTAETIGT)
        _, alt = self.termin_mit_buchung(-24 * 400, Buchung.Status.BESTAETIGT)

        ausgabe = self.rufe("buchungen_pflegen")

        abgelaufen.refresh_from_db(); faellig.refresh_from_db(); alt.refresh_from_db()
        self.assertEqual(abgelaufen.status, Buchung.Status.VERFALLEN)
        self.assertIsNotNone(faellig.erinnerung_am)
        self.assertEqual(alt.email, "")
        self.assertIn("Pflege abgeschlossen", ausgabe)

    def test_erinnerungen_lassen_sich_abschalten(self):
        _, faellig = self.termin_mit_buchung(5, Buchung.Status.BESTAETIGT)

        self.rufe("buchungen_pflegen", "--ohne-erinnerungen")

        faellig.refresh_from_db()
        self.assertIsNone(faellig.erinnerung_am)
        self.assertEqual(len(django_mail.outbox), 0)

    def test_anonymisierung_laesst_sich_abschalten(self):
        _, alt = self.termin_mit_buchung(-24 * 400, Buchung.Status.BESTAETIGT)

        self.rufe("buchungen_pflegen", "--ohne-anonymisierung")

        alt.refresh_from_db()
        self.assertEqual(alt.email, "max@example.org")

    def test_laeuft_ohne_daten_sauber_durch(self):
        self.assertIn("Pflege abgeschlossen", self.rufe("buchungen_pflegen"))


class JobsEinrichten(KommandoBasis):
    def test_legt_alle_zeitplaene_an(self):
        ausgabe = self.rufe("jobs_einrichten")

        self.assertEqual(Schedule.objects.count(), len(JOBS))
        for name in JOBS:
            self.assertTrue(Schedule.objects.filter(name=name).exists())
            self.assertIn(name, ausgabe)

    def test_zweiter_lauf_erzeugt_keine_duplikate(self):
        self.rufe("jobs_einrichten")
        self.rufe("jobs_einrichten")

        self.assertEqual(Schedule.objects.count(), len(JOBS))

    def test_zeitplaene_laufen_unbegrenzt_weiter(self):
        self.rufe("jobs_einrichten")

        for plan in Schedule.objects.all():
            self.assertEqual(plan.repeats, -1, f"{plan.name} würde irgendwann aufhören")
            self.assertIsNotNone(plan.next_run)

    def test_veraltete_jobs_werden_entfernt(self):
        Schedule.objects.create(
            name="Alter Job",
            func="termine.jobs.gibt_es_nicht_mehr",
            schedule_type=Schedule.DAILY,
        )

        ausgabe = self.rufe("jobs_einrichten")

        self.assertFalse(Schedule.objects.filter(name="Alter Job").exists())
        self.assertIn("veraltete", ausgabe)

    def test_fremde_zeitplaene_bleiben_unberuehrt(self):
        """Andere Apps dürfen eigene Jobs in derselben Tabelle haben."""
        Schedule.objects.create(
            name="Backup", func="irgendwas.anderes.sichern", schedule_type=Schedule.DAILY
        )

        self.rufe("jobs_einrichten")

        self.assertTrue(Schedule.objects.filter(name="Backup").exists())
