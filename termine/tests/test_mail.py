"""Tests für die Gestalt der E-Mails.

Mails sind der einzige Teil der App, den niemand beim Entwickeln sieht: Sie
entstehen im Hintergrundjob und landen in einem fremden Postfach. Deshalb wird
hier festgehalten, was sonst still kaputtgehen könnte – dass jede Nachricht
beide Fassungen mitbringt, dass die Textfassung vollständig bleibt und dass
die HTML-Fassung die Farben der Webseite trägt.
"""

from __future__ import annotations

import datetime as dt

from django.core import mail as django_mail
from django.test import TestCase, override_settings
from django.utils import timezone

from termine.models import Buchung, Fahrlehrer, Termin, Terminart
from termine.services import mail as mail_service

# Marke und Akzent aus static/css/app.css. Fällt einer davon aus der Vorlage,
# sieht die Mail nicht mehr nach der Fahrschule aus.
MARKE = "#2b5883"
AKZENT = "#c72e2e"


class MailBasis(TestCase):
    def setUp(self):
        self.art = Terminart.objects.create(
            name="Erstberatung",
            dauer_minuten=30,
            ort="Hauptstraße 1",
            beschreibung="Bitte den Ausweis mitbringen.",
        )
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW"
        )
        beginn = (timezone.now() + dt.timedelta(days=5)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        self.termin = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
            status=Termin.Status.GEBUCHT,
        )
        self.buchung = Buchung.objects.create(
            termin=self.termin,
            name="Max Muster",
            email="max@example.org",
            telefon="0170 1234567",
            fuehrerscheinklasse="B",
            nachricht="Ich bin 17 und möchte BF17 machen.",
            status=Buchung.Status.BESTAETIGT,
        )

    def letzte(self):
        """Die zuletzt versendete Nachricht mit Text- und HTML-Fassung."""
        nachricht = django_mail.outbox[-1]
        html = next(
            inhalt for inhalt, typ in nachricht.alternatives if typ == "text/html"
        )
        return nachricht, nachricht.body, html

    def alle_versender(self):
        return (
            mail_service.bestaetigung_anfordern,
            mail_service.buchung_bestaetigt_kunde,
            mail_service.buchung_bestaetigt_fahrlehrer,
            mail_service.storno_kunde,
            mail_service.storno_fahrlehrer,
            mail_service.erinnerung,
        )


class ZweiFassungen(MailBasis):
    def test_jede_mail_bringt_text_und_html_mit(self):
        for versenden in self.alle_versender():
            with self.subTest(versenden.__name__):
                django_mail.outbox.clear()
                self.assertTrue(versenden(self.buchung))
                nachricht, text, html = self.letzte()
                self.assertEqual(len(nachricht.alternatives), 1)
                self.assertTrue(text.strip())
                self.assertIn("<html", html)

    def test_die_textfassung_bleibt_vollwertig(self):
        """Kein „siehe HTML-Fassung": Wer nur Text sieht, sieht den Termin."""
        for versenden in self.alle_versender():
            with self.subTest(versenden.__name__):
                django_mail.outbox.clear()
                versenden(self.buchung)
                _, text, _ = self.letzte()
                self.assertIn(self.art.name, text)
                self.assertNotIn("<", text)

    def test_der_kalendereintrag_haengt_weiter_an(self):
        """Anhang und zweite Fassung dürfen sich nicht gegenseitig verdrängen."""
        mail_service.buchung_bestaetigt_kunde(self.buchung)
        nachricht, _, html = self.letzte()
        self.assertEqual([name for name, _, _ in nachricht.attachments], ["termin.ics"])
        self.assertIn("<html", html)

    def test_der_bestaetigungslink_steht_in_beiden_fassungen(self):
        """Das Double-Opt-in hängt an diesem Link – er darf nirgends fehlen."""
        mail_service.bestaetigung_anfordern(self.buchung)
        _, text, html = self.letzte()
        self.assertIn(self.buchung.bestaetigungs_url, text)
        self.assertIn(self.buchung.bestaetigungs_url, html)


class Gestalt(MailBasis):
    def test_die_html_fassung_traegt_die_farben_der_webseite(self):
        for versenden in self.alle_versender():
            with self.subTest(versenden.__name__):
                django_mail.outbox.clear()
                versenden(self.buchung)
                _, _, html = self.letzte()
                self.assertIn(MARKE, html)
                self.assertIn("background-color:#ebefe7", html)

    def test_die_stile_stehen_am_element(self):
        """Viele Postfächer streichen <style> – tragende Stile müssen inline sein."""
        mail_service.buchung_bestaetigt_kunde(self.buchung)
        _, _, html = self.letzte()
        self.assertIn('style="background-color:#ffffff', html)

    def test_kundenmails_schreiben_die_ueberschrift_wie_die_oeffentliche_seite(self):
        mail_service.buchung_bestaetigt_kunde(self.buchung)
        _, _, html = self.letzte()
        self.assertIn("text-transform:uppercase", html)
        self.assertIn(f"4px double {AKZENT}", html)

    def test_mails_an_die_fahrschule_bleiben_sachlich(self):
        """Der interne Bereich verzichtet auf Versalien und roten Strich."""
        for versenden in (
            mail_service.buchung_bestaetigt_fahrlehrer,
            mail_service.storno_fahrlehrer,
        ):
            with self.subTest(versenden.__name__):
                django_mail.outbox.clear()
                versenden(self.buchung)
                _, _, html = self.letzte()
                self.assertNotIn("text-transform:uppercase", html)
                self.assertNotIn("double", html)

    def test_die_dunkle_fassung_ist_hinterlegt(self):
        mail_service.erinnerung(self.buchung)
        _, _, html = self.letzte()
        self.assertIn("prefers-color-scheme: dark", html)


class RechtlicheLinks(MailBasis):
    """Mails entstehen ohne Request, also ohne Kontextprozessor – die Links
    für den Fuß müssen trotzdem ankommen."""

    @override_settings(
        IMPRESSUM_URL="https://example.org/impressum",
        DATENSCHUTZ_URL="https://example.org/datenschutz",
    )
    def test_impressum_und_datenschutz_stehen_im_fuss(self):
        mail_service.erinnerung(self.buchung)
        _, _, html = self.letzte()
        self.assertIn("https://example.org/impressum", html)
        self.assertIn("https://example.org/datenschutz", html)

    @override_settings(IMPRESSUM_URL="", DATENSCHUTZ_URL="")
    def test_ohne_gepflegte_adressen_bleibt_der_fuss_leer(self):
        mail_service.erinnerung(self.buchung)
        _, _, html = self.letzte()
        self.assertNotIn("Impressum", html)
        self.assertNotIn("Datenschutz", html)


class InterneMail(MailBasis):
    def test_die_fahrlehrermail_zeigt_die_angaben_des_interessenten(self):
        mail_service.buchung_bestaetigt_fahrlehrer(self.buchung)
        _, text, html = self.letzte()
        for fassung in (text, html):
            self.assertIn("Max Muster", fassung)
            self.assertIn("max@example.org", fassung)
            self.assertIn("BF17", fassung)

    def test_die_stornomail_benennt_den_absagenden_lesbar(self):
        self.buchung.storniert_von = "kunde"
        mail_service.storno_fahrlehrer(self.buchung)
        _, text, html = self.letzte()
        self.assertIn("dem Interessenten", text)
        self.assertIn("dem Interessenten", html)

        django_mail.outbox.clear()
        self.buchung.storniert_von = "fahrschule"
        mail_service.storno_fahrlehrer(self.buchung)
        _, text, html = self.letzte()
        self.assertIn("der Fahrschule", text)
        self.assertIn("der Fahrschule", html)
