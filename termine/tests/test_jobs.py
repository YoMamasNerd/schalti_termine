"""Tests für die Hintergrundjobs.

Diese Funktionen ruft django-q2 unbeaufsichtigt auf – meist nachts. Fällt eine
davon aus, merkt es zunächst niemand: Es kommen einfach keine neuen Termine
mehr dazu, oder Erinnerungen bleiben aus. Deshalb wird hier nicht nur geprüft,
dass sie das Richtige tun, sondern auch, dass der Scheduler sie überhaupt
findet.
"""

import datetime as dt
from importlib import import_module

from django.core import mail as django_mail
from django.test import TestCase
from django.utils import timezone

from termine import jobs
from termine.management.commands.jobs_einrichten import JOBS
from termine.models import Buchung, Fahrlehrer, RhythmusRegel, Termin, Terminart


class ZeitplanVerdrahtung(TestCase):
    """Der Scheduler ruft die Jobs über Pfad-Strings auf – die müssen stimmen."""

    def test_jeder_eingeplante_pfad_ist_aufrufbar(self):
        for name, (pfad, *_rest) in JOBS.items():
            with self.subTest(job=name):
                modulname, _, funktionsname = pfad.rpartition(".")
                modul = import_module(modulname)
                funktion = getattr(modul, funktionsname, None)
                self.assertIsNotNone(
                    funktion, f"{pfad} existiert nicht – der Zeitplan liefe ins Leere."
                )
                self.assertTrue(callable(funktion))

    def test_jobs_brauchen_keine_argumente(self):
        """django-q2 ruft ohne Parameter auf."""
        import inspect

        for name, (pfad, *_rest) in JOBS.items():
            with self.subTest(job=name):
                modulname, _, funktionsname = pfad.rpartition(".")
                funktion = getattr(import_module(modulname), funktionsname)
                pflicht = [
                    p
                    for p in inspect.signature(funktion).parameters.values()
                    if p.default is inspect.Parameter.empty
                    and p.kind
                    in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
                ]
                self.assertEqual(pflicht, [], f"{pfad} verlangt Argumente")


class JobBasis(TestCase):
    def setUp(self):
        self.art = Terminart.objects.create(name="Beratung", dauer_minuten=30)
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )

    def termin(self, stunden_voraus=48):
        beginn = (timezone.now() + dt.timedelta(hours=stunden_voraus)).replace(
            minute=0, second=0, microsecond=0
        )
        return Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )

    def buchung(self, termin, status=Buchung.Status.BESTAETIGT, **extra):
        return Buchung.objects.create(
            termin=termin, name="Max Muster", email="max@example.org", status=status, **extra
        )


class Vorausplanen(JobBasis):
    def test_erzeugt_termine_aus_den_regeln(self):
        RhythmusRegel.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            wochentage=[0, 1, 2, 3, 4, 5, 6],
            beginn=dt.time(9, 0),
            ende=dt.time(11, 0),
            gueltig_ab=timezone.localdate(),
        )

        ergebnis = jobs.termine_vorausplanen()

        self.assertGreater(Termin.objects.count(), 0)
        self.assertIn("erstellt", ergebnis)

    def test_laeuft_auch_ohne_regeln_durch(self):
        self.assertIn("erstellt", jobs.termine_vorausplanen())
        self.assertEqual(Termin.objects.count(), 0)


class ReservierungenAufraeumen(JobBasis):
    def test_gibt_abgelaufene_reservierung_frei(self):
        termin = self.termin()
        termin.status = Termin.Status.RESERVIERT
        termin.save()
        self.buchung(
            termin,
            status=Buchung.Status.OFFEN,
            reserviert_bis=timezone.now() - dt.timedelta(minutes=5),
        )

        ergebnis = jobs.reservierungen_aufraeumen()

        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.FREI)
        self.assertIn("1", ergebnis)

    def test_laesst_frische_reservierung_in_ruhe(self):
        termin = self.termin()
        termin.status = Termin.Status.RESERVIERT
        termin.save()
        self.buchung(
            termin,
            status=Buchung.Status.OFFEN,
            reserviert_bis=timezone.now() + dt.timedelta(minutes=20),
        )

        jobs.reservierungen_aufraeumen()

        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.RESERVIERT)


class Erinnerungen(JobBasis):
    def test_verschickt_erinnerung_und_merkt_sich_das(self):
        termin = self.termin(stunden_voraus=5)
        termin.status = Termin.Status.GEBUCHT
        termin.save()
        buchung = self.buchung(termin)

        ergebnis = jobs.erinnerungen()

        buchung.refresh_from_db()
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIsNotNone(buchung.erinnerung_am)
        self.assertIn("1", ergebnis)

        # Ein zweiter Lauf darf nicht erneut erinnern.
        django_mail.outbox.clear()
        jobs.erinnerungen()
        self.assertEqual(len(django_mail.outbox), 0)

    def test_beruecksichtigt_einstellungen_erinnerung_stunden(self):
        from termine.models import FahrschulEinstellungen

        einst = FahrschulEinstellungen.get_solo()
        einst.erinnerung_stunden_vorher = 48
        einst.save()

        termin = self.termin(stunden_voraus=36)
        termin.status = Termin.Status.GEBUCHT
        termin.save()
        self.buchung(termin)
        django_mail.outbox.clear()
        jobs.erinnerungen()
        self.assertEqual(len(django_mail.outbox), 1)

        # Wenn auf 12 Stunden gestellt:
        django_mail.outbox.clear()
        einst.erinnerung_stunden_vorher = 12
        einst.save()

        termin2 = self.termin(stunden_voraus=20)
        termin2.status = Termin.Status.GEBUCHT
        termin2.save()
        buchung2 = self.buchung(termin2)

        jobs.erinnerungen()
        self.assertEqual(len(django_mail.outbox), 0)
        buchung2.refresh_from_db()
        self.assertIsNone(buchung2.erinnerung_am)



class Datenpflege(JobBasis):
    def test_anonymisiert_alte_buchungen(self):
        termin = self.termin(stunden_voraus=-24 * 400)
        buchung = self.buchung(termin)

        ergebnis = jobs.datenpflege()

        buchung.refresh_from_db()
        self.assertEqual(buchung.email, "")
        self.assertIsNotNone(buchung.anonymisiert_am)
        self.assertIn("1", ergebnis)

    def test_laesst_aktuelle_buchungen_unangetastet(self):
        buchung = self.buchung(self.termin())

        jobs.datenpflege()

        buchung.refresh_from_db()
        self.assertEqual(buchung.email, "max@example.org")
        self.assertIsNone(buchung.anonymisiert_am)


class FsmSynchronisieren(JobBasis):
    def test_ruft_sync_service_auf(self):
        ergebnis = jobs.fsm_synchronisieren()
        self.assertIn("FSM-Sync:", ergebnis)
