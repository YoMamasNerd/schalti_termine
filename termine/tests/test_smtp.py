"""Tests für SMTP-Einstellungen, Modell-Konfiguration, Formulare und den Live-Test."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
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

    def test_konfiguration_kommt_aus_der_datenbank(self):
        """Ohne Host läuft der Versand über die Konsole, mit Host über SMTP."""
        self.einst.email_host = ""
        self.einst.save()
        cfg = self.einst.get_effective_email_config()
        self.assertEqual(cfg["quelle"], "datenbank")
        self.assertEqual(cfg["host"], "")
        self.assertEqual(cfg["backend"], "django.core.mail.backends.console.EmailBackend")

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

    def test_form_lässt_nur_eine_verschlüsselung_zu(self):
        daten = {
            "email_host": "smtp.beispiel.de",
            "email_port": 587,
            "email_user": "user@beispiel.de",
            "email_password": "pass",
            "email_from": "Info <info@beispiel.de>",
        }
        form = SmtpEinstellungenForm(
            data={**daten, "email_use_tls": True, "email_use_ssl": False}
        )
        self.assertTrue(form.is_valid(), form.errors)

        form = SmtpEinstellungenForm(
            data={**daten, "email_use_tls": True, "email_use_ssl": True}
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
    def test_auth_erfolgreich_starttls(self):
        with patch("smtplib.SMTP") as mock_smtp_cls:
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

    def test_auth_erfolgreich_ssl(self):
        with patch("smtplib.SMTP_SSL") as mock_ssl_cls:
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

    def test_unvollstaendige_eingaben_werden_abgewiesen(self):
        ergebnis = teste_smtp_authentifizierung(host="", port=587)
        self.assertFalse(ergebnis.ok)
        self.assertIn("Kein SMTP-Host", ergebnis.meldung)

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

        with patch("smtplib.SMTP") as mock_smtp_cls:
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

    def test_auth_fehler_535(self):
        import smtplib

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_server.has_extn.return_value = True
            mock_server.login.side_effect = smtplib.SMTPAuthenticationError(
                535, b"Authentication credentials invalid"
            )
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

    def test_timeout_fehler(self):
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_cls.side_effect = socket.timeout("timed out")
            ergebnis = teste_smtp_authentifizierung(host="smtp.unreachable.org")
        self.assertFalse(ergebnis.ok)
        self.assertIn("Zeitüberschreitung", ergebnis.meldung)

    def test_dns_aufloesungsfehler(self):
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_cls.side_effect = socket.gaierror(-2, "Name or service not known")
            ergebnis = teste_smtp_authentifizierung(host="nicht-existent.invalid")
        self.assertFalse(ergebnis.ok)
        self.assertIn("DNS-Fehler", ergebnis.meldung)

    def test_test_email_wird_versendet_oder_abgewiesen(self):
        with patch(
            "termine.services.smtp.teste_smtp_authentifizierung"
        ) as mock_auth, patch("django.core.mail.EmailMultiAlternatives.send") as mock_send:
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

    def test_smtp_test_ajax_erfolg(self):
        with patch(
            "termine.staff_views.einstellungen.teste_smtp_authentifizierung"
        ) as mock_test:
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

    def test_smtp_test_ajax_mail_versand(self):
        with patch("termine.staff_views.einstellungen.sende_test_email") as mock_mail:
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

    def test_nur_inhaber_darf_smtp_bedienen(self):
        """Mitarbeiter können weder die Konfiguration ändern noch den Test auslösen."""
        self.client.force_login(self.mitarbeiter_user)

        self.client.post(
            reverse("termine:einstellungen"),
            {
                "form_art": "smtp",
                "email_host": "hacked.smtp.org",
            },
        )
        self.globale_einst.refresh_from_db()
        self.assertNotEqual(self.globale_einst.email_host, "hacked.smtp.org")

        antwort = self.client.post(reverse("termine:smtp_test"), {})
        self.assertEqual(antwort.status_code, 403)
