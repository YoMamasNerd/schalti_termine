"""Tests für SMTP-Einstellungen, Modell-Konfiguration, Formulare und den Live-Test."""

from __future__ import annotations

import socket
import ssl
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.mail.backends.smtp import EmailBackend
from django.test import TestCase, override_settings
from django.urls import reverse

from termine.forms import SmtpEinstellungenForm
from termine.models import FahrschulEinstellungen
from termine.services import mail as mail_service
from termine.services.smtp import (
    SmtpTestErgebnis,
    sende_test_email,
    teste_smtp_authentifizierung,
)


class SmtpModellUndFormTest(TestCase):
    def setUp(self):
        self.einst = FahrschulEinstellungen.get_solo()

    def test_standard_ohne_host(self):
        self.einst.email_host = ""
        self.einst.save()
        cfg = self.einst.get_effective_email_config()
        self.assertEqual(cfg["quelle"], "datenbank")
        self.assertEqual(cfg["host"], "")
        self.assertEqual(cfg["backend"], "django.core.mail.backends.console.EmailBackend")

    def test_db_konfiguration_hat_vorrang(self):
        self.einst.email_host = "smtp.schaltwerk.de"
        self.einst.email_port = 587
        self.einst.email_user = "beratung@schaltwerk.de"
        self.einst.email_password = "super-geheim-123"
        self.einst.email_use_tls = True
        self.einst.email_use_ssl = False
        self.einst.email_from = "Fahrschule Schaltwerk <beratung@schaltwerk.de>"
        self.einst.save()

        cfg = self.einst.get_effective_email_config()
        self.assertEqual(cfg["quelle"], "datenbank")
        self.assertEqual(cfg["host"], "smtp.schaltwerk.de")
        self.assertEqual(cfg["port"], 587)
        self.assertEqual(cfg["user"], "beratung@schaltwerk.de")
        self.assertEqual(cfg["password"], "super-geheim-123")
        self.assertTrue(cfg["use_tls"])
        self.assertFalse(cfg["use_ssl"])
        self.assertEqual(cfg["from_email"], "Fahrschule Schaltwerk <beratung@schaltwerk.de>")

    def test_form_validiert_korrekt(self):
        form = SmtpEinstellungenForm(
            data={
                "email_host": "smtp.beispiel.de",
                "email_port": 587,
                "email_user": "user@beispiel.de",
                "email_password": "pass",
                "email_use_tls": True,
                "email_use_ssl": False,
                "email_from": "Info <info@beispiel.de>",
            }
        )
        self.assertTrue(form.is_valid())

    def test_form_schliesst_tls_und_ssl_gleichzeitig_aus(self):
        form = SmtpEinstellungenForm(
            data={
                "email_host": "smtp.beispiel.de",
                "email_port": 587,
                "email_user": "user@beispiel.de",
                "email_password": "pass",
                "email_use_tls": True,
                "email_use_ssl": True,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email_use_ssl", form.errors)

    def test_mail_service_get_from_und_kontakt_email(self):
        self.einst.email_from = "Fahrschule <info@fahrschule.de>"
        self.einst.save()
        self.assertEqual(mail_service.get_from_email(), "Fahrschule <info@fahrschule.de>")
        self.assertEqual(mail_service.get_kontakt_email(), "info@fahrschule.de")

        self.einst.email_from = "kontakt@fahrschule.de"
        self.einst.save()
        self.assertEqual(mail_service.get_kontakt_email(), "kontakt@fahrschule.de")


class SmtpLiveTestServiceTest(TestCase):
    @patch("smtplib.SMTP")
    def test_auth_erfolgreich_starttls(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_server.ehlo.return_value = (250, b"ok")
        mock_server.has_extn.return_value = True
        mock_smtp_cls.return_value = mock_server

        ergebnis = teste_smtp_authentifizierung(
            host="smtp.strato.de",
            port=587,
            user="user@strato.de",
            password="secretpassword",
            use_tls=True,
            use_ssl=False,
        )

        self.assertTrue(ergebnis.ok)
        self.assertIn("erfolgreich", ergebnis.meldung)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user@strato.de", "secretpassword")
        mock_server.quit.assert_called_once()

    @patch("smtplib.SMTP_SSL")
    def test_auth_erfolgreich_ssl(self, mock_ssl_cls):
        mock_server = MagicMock()
        mock_server.ehlo.return_value = (250, b"ok")
        mock_ssl_cls.return_value = mock_server

        ergebnis = teste_smtp_authentifizierung(
            host="smtp.gmail.com",
            port=465,
            user="user@gmail.com",
            password="app-password",
            use_tls=False,
            use_ssl=True,
        )

        self.assertTrue(ergebnis.ok)
        mock_server.login.assert_called_once_with("user@gmail.com", "app-password")
        mock_server.quit.assert_called_once()

    def test_kein_host_angegeben(self):
        ergebnis = teste_smtp_authentifizierung(host="", port=587)
        self.assertFalse(ergebnis.ok)
        self.assertIn("Kein SMTP-Host", ergebnis.meldung)

    def test_benutzer_ohne_passwort(self):
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_server.has_extn.return_value = True
            mock_smtp_cls.return_value = mock_server
            ergebnis = teste_smtp_authentifizierung(
                host="smtp.example.org",
                user="admin",
                password="",
                use_tls=False,
            )
            self.assertFalse(ergebnis.ok)
            self.assertIn("kein Passwort", ergebnis.meldung)

    @patch("smtplib.SMTP")
    def test_starttls_nicht_unterstuetzt(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_server.has_extn.return_value = False
        mock_smtp_cls.return_value = mock_server

        ergebnis = teste_smtp_authentifizierung(
            host="smtp.example.org",
            port=587,
            use_tls=True,
            use_ssl=False,
        )
        self.assertFalse(ergebnis.ok)
        self.assertIn("STARTTLS", ergebnis.meldung)

    @patch("smtplib.SMTP")
    def test_auth_fehler_535(self, mock_smtp_cls):
        import smtplib

        mock_server = MagicMock()
        mock_server.has_extn.return_value = True
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Authentication credentials invalid")
        mock_smtp_cls.return_value = mock_server

        ergebnis = teste_smtp_authentifizierung(
            host="smtp.example.org",
            user="falsch@example.org",
            password="wrong",
            use_tls=True,
        )
        self.assertFalse(ergebnis.ok)
        self.assertIn("Authentifizierung", ergebnis.meldung)
        self.assertIn("535", ergebnis.meldung)

    @patch("smtplib.SMTP")
    def test_timeout_fehler(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = socket.timeout("timed out")
        ergebnis = teste_smtp_authentifizierung(host="smtp.unreachable.org")
        self.assertFalse(ergebnis.ok)
        self.assertIn("Zeitüberschreitung", ergebnis.meldung)

    @patch("smtplib.SMTP")
    def test_dns_aufloesungsfehler(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = socket.gaierror(-2, "Name or service not known")
        ergebnis = teste_smtp_authentifizierung(host="nicht-existent.invalid")
        self.assertFalse(ergebnis.ok)
        self.assertIn("DNS-Fehler", ergebnis.meldung)

    @patch("termine.services.smtp.teste_smtp_authentifizierung")
    @patch("django.core.mail.EmailMultiAlternatives.send")
    def test_sende_test_email_erfolgreich(self, mock_send, mock_auth):
        mock_auth.return_value = SmtpTestErgebnis(True, "Auth ok")
        mock_send.return_value = 1

        ergebnis = sende_test_email(
            empfaenger="tester@beispiel.de",
            host="smtp.beispiel.de",
            port=587,
            user="user@beispiel.de",
            password="pwd",
            from_email="Info <info@beispiel.de>",
        )
        self.assertTrue(ergebnis.ok)
        self.assertIn("erfolgreich an „tester@beispiel.de“", ergebnis.meldung)
        mock_send.assert_called_once()

    def test_sende_test_email_ungueltige_adresse(self):
        ergebnis = sende_test_email(
            empfaenger="ungueltig",
            host="smtp.beispiel.de",
        )
        self.assertFalse(ergebnis.ok)
        self.assertIn("Ungültige", ergebnis.meldung)


class SmtpViewsTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.chef = User.objects.create_superuser("chef", password="geheim123")
        self.mitarbeiter_user = User.objects.create_user("mitarbeiter", password="geheim123")
        from termine.models import Fahrlehrer

        self.lehrer = Fahrlehrer.objects.create(
            name="Max",
            email="max@example.org",
            benutzer=self.mitarbeiter_user,
        )
        self.globale_einst = FahrschulEinstellungen.get_solo()

    def test_inhaber_kann_smtp_speichern(self):
        self.client.force_login(self.chef)
        url = reverse("termine:einstellungen")

        antwort = self.client.post(
            f"{url}?fahrlehrer={self.lehrer.slug}#tab-system",
            {
                "form_art": "smtp",
                "email_host": "smtp.fahrschule.de",
                "email_port": "587",
                "email_user": "kontakt@fahrschule.de",
                "email_password": "meinpasswort",
                "email_use_tls": "on",
                "email_from": "Fahrschule <kontakt@fahrschule.de>",
            },
        )
        self.assertEqual(antwort.status_code, 302)

        self.globale_einst.refresh_from_db()
        self.assertEqual(self.globale_einst.email_host, "smtp.fahrschule.de")
        self.assertEqual(self.globale_einst.email_port, 587)
        self.assertEqual(self.globale_einst.email_user, "kontakt@fahrschule.de")
        self.assertEqual(self.globale_einst.email_password, "meinpasswort")
        self.assertTrue(self.globale_einst.email_use_tls)
        self.assertEqual(self.globale_einst.email_from, "Fahrschule <kontakt@fahrschule.de>")

    def test_inhaber_kann_smtp_via_ajax_speichern(self):
        self.client.force_login(self.chef)
        url = reverse("termine:einstellungen")

        antwort = self.client.post(
            url,
            {
                "form_art": "smtp",
                "email_host": "mail.schaltwerk.de",
                "email_port": "465",
                "email_user": "info@schaltwerk.de",
                "email_password": "geheim",
                "email_use_ssl": "on",
                "email_from": "info@schaltwerk.de",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(antwort.status_code, 200)
        data = antwort.json()
        self.assertTrue(data["ok"])

        self.globale_einst.refresh_from_db()
        self.assertEqual(self.globale_einst.email_host, "mail.schaltwerk.de")
        self.assertEqual(self.globale_einst.email_port, 465)
        self.assertTrue(self.globale_einst.email_use_ssl)

    def test_normaler_mitarbeiter_kann_smtp_nicht_speichern(self):
        self.client.force_login(self.mitarbeiter_user)
        url = reverse("termine:einstellungen")

        # Nicht-Inhaber POST mit form_art=smtp wird ignoriert
        antwort = self.client.post(
            url,
            {
                "form_art": "smtp",
                "email_host": "hacked.smtp.org",
            },
        )
        self.globale_einst.refresh_from_db()
        self.assertNotEqual(self.globale_einst.email_host, "hacked.smtp.org")

    @patch("termine.staff_views.teste_smtp_authentifizierung")
    def test_smtp_test_ajax_erfolg(self, mock_test):
        mock_test.return_value = SmtpTestErgebnis(True, "Authentifizierung erfolgreich!")
        self.client.force_login(self.chef)

        antwort = self.client.post(
            reverse("termine:smtp_test"),
            {
                "email_host": "smtp.strato.de",
                "email_port": "587",
                "email_user": "kontakt@strato.de",
                "email_password": "pwd",
                "email_use_tls": "true",
                "aktion": "auth",
            },
        )
        self.assertEqual(antwort.status_code, 200)
        data = antwort.json()
        self.assertTrue(data["ok"])
        self.assertIn("Authentifizierung erfolgreich", data["meldung"])

    @patch("termine.staff_views.sende_test_email")
    def test_smtp_test_ajax_mail_versand(self, mock_mail):
        mock_mail.return_value = SmtpTestErgebnis(True, "Test-E-Mail gesendet")
        self.client.force_login(self.chef)

        antwort = self.client.post(
            reverse("termine:smtp_test"),
            {
                "email_host": "smtp.strato.de",
                "test_empfaenger": "admin@strato.de",
                "aktion": "mail",
            },
        )
        self.assertEqual(antwort.status_code, 200)
        data = antwort.json()
        self.assertTrue(data["ok"])
        mock_mail.assert_called_once()

    def test_smtp_test_ajax_nur_fuer_inhaber(self):
        self.client.force_login(self.mitarbeiter_user)
        antwort = self.client.post(reverse("termine:smtp_test"), {})
        self.assertEqual(antwort.status_code, 403)
