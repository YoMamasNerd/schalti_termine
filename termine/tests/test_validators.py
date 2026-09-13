"""Tests für E-Mail- und Telefonnummern-Validatoren in Buchungen."""

from django.core.exceptions import ValidationError
from django.test import TestCase

from termine.forms import BuchungsForm
from termine.validators import validate_email_address, validate_phone_number


class EmailValidatorTest(TestCase):
    def test_gueltige_adressen(self):
        self.assertEqual(validate_email_address("max@example.org"), "max@example.org")
        self.assertEqual(validate_email_address("  MAX@Example.COM  "), "max@example.com")
        self.assertEqual(validate_email_address("anna-maria.berger@fahrschule.berlin"), "anna-maria.berger@fahrschule.berlin")
        self.assertEqual(validate_email_address("schueler+test@sub.domain.de"), "schueler+test@sub.domain.de")

    def test_dsgvo_anonymisierung_erlaubt(self):
        self.assertEqual(validate_email_address("Gelöscht"), "Gelöscht")

    def test_ungueltige_adressen(self):
        ungueltige = [
            "",
            "   ",
            "keine-adresse",
            "max@",
            "@example.org",
            "max@localhost",
            "max@domain",
            "max@domain.c",  # TLD zu kurz (<2)
            "max@domain..de",  # Doppelte Punkte
            "max@.de",
            "max muster@example.org",
        ]
        for adr in ungueltige:
            with self.subTest(adresse=adr):
                with self.assertRaises(ValidationError):
                    validate_email_address(adr)


class TelefonValidatorTest(TestCase):
    def test_freiwillig_leer_erlaubt(self):
        self.assertEqual(validate_phone_number(""), "")
        self.assertEqual(validate_phone_number(None), "")
        self.assertEqual(validate_phone_number("   "), "")

    def test_dsgvo_anonymisierung_erlaubt(self):
        self.assertEqual(validate_phone_number("Gelöscht"), "Gelöscht")

    def test_gueltige_nummern(self):
        faelle = [
            ("0170 1234567", "0170 1234567"),
            ("+49 170 1234567", "+49 170 1234567"),
            ("0049 170 1234567", "0049 170 1234567"),
            ("030 / 123456", "030 / 123456"),
            ("(030) 1234-567", "(030) 1234-567"),
            ("  0151   12345678  ", "0151 12345678"),
        ]
        for eingabe, erwartet in faelle:
            with self.subTest(eingabe=eingabe):
                self.assertEqual(validate_phone_number(eingabe), erwartet)

    def test_ungueltige_nummern(self):
        ungueltige = [
            "12345",  # Zu kurz (< 6 Ziffern)
            "123",
            "keine",  # Buchstaben
            "0170 abc 123",
            "00000000",  # Reine Wiederholung
            "1111111111",
            "+49 170 +49 123",  # Mehrere Plus
            "49+170123456",  # Plus nicht am Anfang
            "0170 1234567890123456",  # Zu lang (> 15 Ziffern)
        ]
        for nr in ungueltige:
            with self.subTest(nummer=nr):
                with self.assertRaises(ValidationError):
                    validate_phone_number(nr)


class BuchungsFormValidationTest(TestCase):
    def basis_daten(self):
        return {
            "name": "Erika Mustermann",
            "email": "erika@example.org",
            "telefon": "",
            "datenschutz": True,
            "website": "",
        }

    def test_formular_gueltig_ohne_telefon(self):
        daten = self.basis_daten()
        daten["telefon"] = ""
        form = BuchungsForm(data=daten)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["telefon"], "")

    def test_formular_gueltig_mit_gueltigem_telefon(self):
        daten = self.basis_daten()
        daten["telefon"] = "0170 / 9876543"
        form = BuchungsForm(data=daten)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["telefon"], "0170 / 9876543")

    def test_formular_lehnt_ungueltige_email_ab(self):
        # Fall A: Völlig ungültige Syntax
        daten = self.basis_daten()
        daten["email"] = "falsch"
        form = BuchungsForm(data=daten)
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
        self.assertEqual(len(form.errors["email"]), 1, "Es darf genau eine Fehlermeldung bei falscher Mail erscheinen")

        # Fall B: Unvollständige Domain
        daten["email"] = "ungueltig@domain"
        form = BuchungsForm(data=daten)
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
        self.assertEqual(len(form.errors["email"]), 1, "Es darf genau eine Fehlermeldung bei falscher Mail erscheinen")

    def test_formular_lehnt_ungueltiges_telefon_ab(self):
        daten = self.basis_daten()
        daten["telefon"] = "123"
        form = BuchungsForm(data=daten)
        self.assertFalse(form.is_valid())
        self.assertIn("telefon", form.errors)
        self.assertEqual(len(form.errors["telefon"]), 1)
