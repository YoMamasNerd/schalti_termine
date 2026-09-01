"""Tests für die Fehlerpfade – was passiert, wenn etwas schiefgeht.

Diese Fälle treten selten auf, aber wenn, dann im laufenden Betrieb und ohne
Zuschauer. Der Anspruch: Ein streikender Mailserver darf keine Buchung
zerstören, und zwei gleichzeitige Interessenten dürfen nicht beide denselben
Termin bekommen.
"""

import datetime as dt
from smtplib import SMTPException
from unittest.mock import patch

from django.core import mail as django_mail
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from termine.models import Buchung, Fahrlehrer, Sperrzeit, Termin, Terminart
from termine.services import buchung as buchungs_service
from termine.services.feiertage import feiertage_im_zeitraum, ist_feiertag


class AusfallBasis(TestCase):
    def setUp(self):
        from termine.models import FahrschulEinstellungen

        einst = FahrschulEinstellungen.get_solo()
        einst.vorlauf_stunden = 1
        einst.save()

        self.art = Terminart.objects.create(name="Beratung", dauer_minuten=30)
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )
        self.termin = self.neuer_termin()

    def neuer_termin(self, tage_voraus=5):
        beginn = (timezone.now() + dt.timedelta(days=tage_voraus)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        return Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )

    def reservieren(self, termin=None, **extra):
        daten = {"name": "Max Muster", "email": "max@example.org"}
        daten.update(extra)
        with self.captureOnCommitCallbacks(execute=True):
            return buchungs_service.reservieren((termin or self.termin).pk, **daten)


class MailserverStreikt(AusfallBasis):
    """Zugesichert in services/mail.py: Ein Versandfehler kippt die Buchung nicht."""

    def test_reservierung_ueberlebt_einen_smtp_fehler(self):
        with self.assertLogs("termine.services.mail", level="ERROR") as protokoll:
            with patch(
                "django.core.mail.EmailMessage.send", side_effect=SMTPException("Server weg")
            ):
                buchung = self.reservieren()

        self.assertTrue(any("konnte nicht versendet werden" in z for z in protokoll.output))

        buchung.refresh_from_db()
        self.termin.refresh_from_db()
        self.assertEqual(buchung.status, Buchung.Status.OFFEN)
        self.assertEqual(self.termin.status, Termin.Status.RESERVIERT)

    def test_bestaetigung_ueberlebt_einen_smtp_fehler(self):
        buchung = self.reservieren()

        with self.assertLogs("termine.services.mail", level="ERROR"):
            with patch(
                "django.core.mail.EmailMessage.send", side_effect=SMTPException("Server weg")
            ):
                with self.captureOnCommitCallbacks(execute=True):
                    buchungs_service.bestaetigen(buchung)

        buchung.refresh_from_db()
        self.termin.refresh_from_db()
        self.assertEqual(buchung.status, Buchung.Status.BESTAETIGT)
        self.assertEqual(self.termin.status, Termin.Status.GEBUCHT)

    def test_storno_ueberlebt_einen_smtp_fehler(self):
        buchung = self.reservieren()
        with self.captureOnCommitCallbacks(execute=True):
            buchungs_service.bestaetigen(buchung)

        with self.assertLogs("termine.services.mail", level="ERROR"):
            with patch(
                "django.core.mail.EmailMessage.send", side_effect=SMTPException("Server weg")
            ):
                with self.captureOnCommitCallbacks(execute=True):
                    buchungs_service.stornieren(buchung, von="kunde")

        buchung.refresh_from_db()
        self.termin.refresh_from_db()
        self.assertEqual(buchung.status, Buchung.Status.STORNIERT)
        self.assertEqual(self.termin.status, Termin.Status.FREI)

    def test_erinnerung_ohne_versand_wird_nicht_abgehakt(self):
        """Scheitert der Versand, muss der nächste Lauf es erneut versuchen."""
        nah = self.neuer_termin(tage_voraus=0)
        nah.beginn = timezone.now() + dt.timedelta(hours=5)
        nah.ende = nah.beginn + dt.timedelta(minutes=30)
        nah.save()
        buchung = self.reservieren(termin=nah)
        with self.captureOnCommitCallbacks(execute=True):
            buchungs_service.bestaetigen(buchung)

        with self.assertLogs("termine.services.mail", level="ERROR"):
            with patch(
                "django.core.mail.EmailMessage.send", side_effect=SMTPException("Server weg")
            ):
                anzahl = buchungs_service.erinnerungen_versenden(stunden_vorher=24)

        buchung.refresh_from_db()
        self.assertEqual(anzahl, 0)
        self.assertIsNone(buchung.erinnerung_am)

        # Sobald der Server wieder da ist, geht die Erinnerung raus.
        django_mail.outbox.clear()
        self.assertEqual(buchungs_service.erinnerungen_versenden(stunden_vorher=24), 1)


class TerminNichtVerfuegbar(AusfallBasis):
    def test_geloeschter_termin(self):
        pk = self.termin.pk
        self.termin.delete()

        with self.assertRaises(buchungs_service.TerminNichtVerfuegbar):
            buchungs_service.reservieren(pk, name="Max", email="max@example.org")

    def test_termin_innerhalb_einer_sperrzeit(self):
        Sperrzeit.objects.create(
            fahrlehrer=self.fahrlehrer,
            beginn=self.termin.beginn - dt.timedelta(hours=1),
            ende=self.termin.ende + dt.timedelta(hours=1),
            grund="Urlaub",
        )

        with self.assertRaises(buchungs_service.TerminNichtVerfuegbar):
            self.reservieren()

    def test_wettlauf_zweier_interessenten(self):
        """Kommt der Unique-Index zuerst, wird daraus eine saubere Absage."""
        with patch(
            "termine.models.Buchung.objects.create",
            side_effect=IntegrityError("nur_eine_aktive_buchung_pro_termin"),
        ):
            with self.assertRaises(buchungs_service.TerminNichtVerfuegbar):
                self.reservieren()

        # Der Termin bleibt frei und buchbar.
        self.termin.refresh_from_db()
        self.assertEqual(self.termin.status, Termin.Status.FREI)
        self.assertEqual(Buchung.objects.count(), 0)

    def test_stornieren_einer_verfallenen_buchung_ist_folgenlos(self):
        buchung = self.reservieren()
        buchung.status = Buchung.Status.VERFALLEN
        buchung.save()

        ergebnis = buchungs_service.stornieren(buchung)

        self.assertEqual(ergebnis.status, Buchung.Status.VERFALLEN)

    def test_bestaetigen_einer_stornierten_buchung_scheitert(self):
        buchung = self.reservieren()
        buchung.status = Buchung.Status.STORNIERT
        buchung.save()

        with self.assertRaises(buchungs_service.BuchungsFehler):
            buchungs_service.bestaetigen(buchung)


class FeiertagsBibliothek(TestCase):
    def test_unbekanntes_bundesland_fliegt_auf(self):
        with self.assertRaises(ValueError):
            ist_feiertag("XX", dt.date(2026, 1, 1))

    def test_leerer_zeitraum_liefert_nichts(self):
        tag = dt.date(2026, 7, 15)  # mitten im Sommer, kein Feiertag
        self.assertEqual(feiertage_im_zeitraum("BW", tag, tag), {})
