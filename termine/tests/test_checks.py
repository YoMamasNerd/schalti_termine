"""Tests für die Systemprüfungen aus termine/checks.py."""

import os
from unittest import mock

from django.apps import apps
from django.test import SimpleTestCase, TestCase, override_settings

from termine.checks import (
    pruefe_mailversand,
    pruefe_oeffentliche_adresse,
    pruefe_platzhalter,
    pruefe_reservierungsdauer,
)
from termine.models import FahrschulEinstellungen


def ids(meldungen):
    return {m.id for m in meldungen}


class AppKonfiguration(SimpleTestCase):
    def test_eigene_appconfig_ist_aktiv(self):
        konfig = apps.get_app_config("termine")
        self.assertEqual(type(konfig).__name__, "TermineConfig")
        self.assertEqual(konfig.verbose_name, "Beratungstermine")


class OeffentlicheAdresse(SimpleTestCase):
    @override_settings(DEBUG=True, SITE_BASE_URL="http://localhost:8000")
    def test_bei_debug_wird_nicht_gemeckert(self):
        self.assertEqual(pruefe_oeffentliche_adresse(None), [])

    @override_settings(DEBUG=False, SITE_BASE_URL="http://localhost:8000")
    def test_localhost_im_betrieb_ist_ein_fehler(self):
        self.assertIn("termine.E001", ids(pruefe_oeffentliche_adresse(None)))

    @override_settings(DEBUG=False, SITE_BASE_URL="http://127.0.0.1:8000")
    def test_auch_die_ip_wird_erkannt(self):
        self.assertIn("termine.E001", ids(pruefe_oeffentliche_adresse(None)))

    @override_settings(DEBUG=False, SITE_BASE_URL="http://termine.fahrschule.de")
    def test_ohne_tls_gibt_es_eine_warnung(self):
        meldungen = ids(pruefe_oeffentliche_adresse(None))
        self.assertIn("termine.W001", meldungen)
        self.assertNotIn("termine.E001", meldungen)

    @override_settings(DEBUG=False, SITE_BASE_URL="https://termine.fahrschule.de")
    def test_richtige_adresse_ist_still(self):
        self.assertEqual(pruefe_oeffentliche_adresse(None), [])


class Mailversand(TestCase):
    def setUp(self):
        self.einst = FahrschulEinstellungen.get_solo()

    @override_settings(DEBUG=True)
    def test_bei_debug_wird_nicht_gemeckert(self):
        self.einst.email_host = ""
        self.einst.save()
        self.assertEqual(pruefe_mailversand(None), [])

    @override_settings(DEBUG=False)
    def test_ohne_smtp_host_gibt_es_eine_warnung(self):
        self.einst.email_host = ""
        self.einst.save()
        self.assertIn("termine.W002", ids(pruefe_mailversand(None)))

    @override_settings(DEBUG=False)
    def test_beispielserver_ist_ein_fehler(self):
        self.einst.email_host = "smtp.example.org"
        self.einst.save()
        self.assertIn("termine.E003", ids(pruefe_mailversand(None)))

    @override_settings(DEBUG=False)
    def test_echte_konfiguration_ist_still(self):
        self.einst.email_host = "mail.fahrschule-schaltwerk.de"
        self.einst.save()
        self.assertEqual(pruefe_mailversand(None), [])


class Reservierungsdauer(SimpleTestCase):
    @override_settings(RESERVATION_MINUTES=2)
    def test_zu_kurze_frist_warnt(self):
        self.assertIn("termine.W003", ids(pruefe_reservierungsdauer(None)))

    @override_settings(RESERVATION_MINUTES=30)
    def test_uebliche_frist_ist_still(self):
        self.assertEqual(pruefe_reservierungsdauer(None), [])


class UnveraenderteGeheimnisse(SimpleTestCase):
    """Djangos eigene Prüfung warnt hier nur – Warnungen halten nichts auf."""

    @override_settings(DEBUG=True, SECRET_KEY="bitte-aendern")
    def test_bei_debug_wird_nicht_gemeckert(self):
        self.assertEqual(pruefe_platzhalter(None), [])

    @override_settings(DEBUG=False, SECRET_KEY="bitte-aendern")
    def test_schluessel_aus_der_beispieldatei(self):
        self.assertIn("termine.E004", ids(pruefe_platzhalter(None)))

    @override_settings(DEBUG=False, SECRET_KEY="unsicher-nur-fuer-entwicklung")
    def test_auch_die_vorgabe_aus_den_settings(self):
        self.assertIn("termine.E004", ids(pruefe_platzhalter(None)))

    @override_settings(DEBUG=False, SECRET_KEY="ein-langer-echt-zufaelliger-wert-xyz")
    def test_echter_schluessel_ist_still(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pruefe_platzhalter(None), [])

    @override_settings(DEBUG=False, SECRET_KEY="ein-langer-echt-zufaelliger-wert-xyz")
    def test_datenbankpasswort_aus_der_beispieldatei(self):
        with mock.patch.dict(os.environ, {"POSTGRES_PASSWORD": "bitte-aendern"}):
            meldungen = pruefe_platzhalter(None)
        self.assertIn("termine.E005", ids(meldungen))
        self.assertIn("POSTGRES_PASSWORD", meldungen[0].msg)
