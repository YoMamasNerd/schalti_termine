"""Sicherheitstests mit echten Angriffszeichenketten.

Django maskiert in Vorlagen von sich aus – aber genau darauf verlässt man sich
so lange, bis jemand ein `|safe` einbaut, um „die Nachricht schöner
darzustellen". Diese Tests schicken die üblichen Nutzlasten durch eine echte
Buchung und lesen nach, was auf den Seiten ankommt: bei der Kundin selbst und
im internen Bereich, wo die Fahrschule die Nachricht liest.
"""

from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.core import mail as django_mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models import Buchung, Fahrlehrer, Termin, Terminart
from termine.services import buchung as buchungs_service

# Die vier Muster decken die Stellen ab, an denen maskiert werden muss:
# Elementinhalt, Attributwert, einfache Anführungszeichen und der
# Ereignis-Handler ohne spitze Klammer.
NUTZLASTEN = {
    "element": "<script>alert('xss')</script>",
    "attribut": '"><img src=x onerror=alert(1)>',
    "einfach": "'><svg/onload=alert(1)>",
    "javascript": "javascript:alert(document.cookie)",
}


class Basis(TestCase):
    def setUp(self):
        self.art = Terminart.objects.create(name="Erstberatung", dauer_minuten=30)
        self.anna = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )
        beginn = (timezone.now() + dt.timedelta(days=3)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        self.termin = Termin.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )

    def buchung_mit(self, **felder) -> Buchung:
        daten = {
            "name": "Lena Hoffmann",
            "email": "lena@example.org",
            "telefon": "0176 1234567",
            "nachricht": "",
        }
        daten.update(felder)
        return buchungs_service.reservieren(self.termin.pk, **daten)


class KeinScriptKommtDurch(Basis):
    def test_kundenseite_maskiert_name_und_nachricht(self):
        buchung = self.buchung_mit(
            name=NUTZLASTEN["element"], nachricht=NUTZLASTEN["attribut"]
        )
        antwort = self.client.get(reverse("termine:buchung", args=[buchung.token]))
        inhalt = antwort.content.decode()

        self.assertNotIn("<script>alert", inhalt)
        self.assertNotIn("<img src=x", inhalt)
        # Maskiert muss es trotzdem lesbar dastehen – sonst wäre der Test blind
        # gegenüber einer Seite, die den Namen gar nicht anzeigt.
        self.assertIn("&lt;script&gt;", inhalt)

    def test_buchungsliste_der_fahrschule_maskiert_ebenso(self):
        buchung = self.buchung_mit(
            name=NUTZLASTEN["attribut"], nachricht=NUTZLASTEN["element"]
        )
        buchungs_service.bestaetigen(buchung)

        chef = get_user_model().objects.create_superuser("chef", password="geheim123")
        self.client.force_login(chef)
        inhalt = self.client.get(reverse("termine:buchungen")).content.decode()

        # Geprüft wird auf die spitzen Klammern, nicht auf „onerror=alert":
        # Letzteres steht auch in der maskierten Fassung noch da, weil daran
        # nichts zu maskieren ist – es ist dort bloß Text und kein Handler.
        self.assertNotIn("<script>alert", inhalt)
        self.assertNotIn("<img src=x", inhalt)
        self.assertIn("&lt;img src=x", inhalt)
        self.assertIn("&quot;&gt;", inhalt)

    def test_tagesplanung_maskiert_den_namen_am_termin(self):
        buchung = self.buchung_mit(name=NUTZLASTEN["einfach"])
        buchungs_service.bestaetigen(buchung)

        chef = get_user_model().objects.create_superuser("chef", password="geheim123")
        self.client.force_login(chef)
        inhalt = self.client.get(reverse("termine:tagesplanung")).content.decode()

        self.assertNotIn("<svg/onload", inhalt)

    def test_nachricht_landet_unveraendert_in_der_datenbank(self):
        # Maskiert wird beim Ausgeben, nicht beim Speichern. Sonst stünde in
        # der Datenbank irgendwann &amp;lt; statt dem, was der Kunde schrieb.
        buchung = self.buchung_mit(nachricht=NUTZLASTEN["element"])
        buchung.refresh_from_db()
        self.assertEqual(buchung.nachricht, NUTZLASTEN["element"])


class MailUndKalender(Basis):
    def test_kein_zeilenumbruch_im_mailkopf(self):
        # Ein Name mit Zeilenumbruch wäre der klassische Versuch, eigene
        # Kopfzeilen unterzuschieben. Django lehnt das ab – hier steht, dass
        # es die Buchung trotzdem nicht zerreißt.
        buchung = self.buchung_mit(name="Max\nBcc: fremder@example.org")
        buchungs_service.bestaetigen(buchung)

        for nachricht in django_mail.outbox:
            self.assertNotIn("\n", nachricht.subject)
            self.assertNotIn("Bcc:", nachricht.subject)

    def test_kalendereintrag_bricht_nicht_aus_seinem_feld_aus(self):
        buchung = self.buchung_mit(
            name="Max Muster", nachricht="Zeile eins\r\nSUMMARY:Untergeschoben"
        )
        buchung = buchungs_service.bestaetigen(buchung)

        from termine.services.ics import buchung_als_ics

        text = buchung_als_ics(buchung).decode()
        # Genau ein SUMMARY-Feld: Der Umbruch aus der Nachricht darf keine
        # zweite Eigenschaft erzeugen.
        summary_zeilen = [line for line in text.splitlines() if line.startswith("SUMMARY:")]
        self.assertEqual(len(summary_zeilen), 1)


class Zugriffsgrenzen(Basis):
    def test_fremder_token_liefert_404(self):
        antwort = self.client.get(reverse("termine:buchung", args=["a" * 43]))
        self.assertEqual(antwort.status_code, 404)

    def test_token_ist_lang_genug_gegen_raten(self):
        buchung = self.buchung_mit()
        # secrets.token_urlsafe(32) – 256 Bit Zufall, base64-kodiert.
        self.assertGreaterEqual(len(buchung.token), 43)

    def test_kalender_abo_haengt_am_token_nicht_am_namen(self):
        antwort = self.client.get(reverse("termine:ics_feed", args=["falscher-token"]))
        self.assertEqual(antwort.status_code, 404)


class Schutzkoepfe(Basis):
    """Die Kopfzeilen, die der Browser zum Mitschützen braucht."""

    def test_seite_laesst_sich_nicht_in_fremde_rahmen_setzen(self):
        antwort = self.client.get(reverse("termine:start"))
        self.assertEqual(antwort.headers["X-Frame-Options"], "DENY")

    def test_kein_raten_am_inhaltstyp(self):
        antwort = self.client.get(reverse("termine:start"))
        self.assertEqual(antwort.headers["X-Content-Type-Options"], "nosniff")

    def test_der_token_wandert_nicht_im_referer_zu_fremden_seiten(self):
        # Die Terminseite trägt den Zugangs-Token in der Adresse und verlinkt
        # im Fuß auf Impressum und Datenschutz – oft auf einer anderen Domain.
        # Ohne diese Kopfzeile stünde der Token im Referer, den der Browser
        # dorthin mitschickt.
        buchung = self.buchung_mit()
        antwort = self.client.get(reverse("termine:buchung", args=[buchung.token]))
        self.assertEqual(antwort.headers["Referrer-Policy"], "same-origin")


class AnmeldungLeitetNichtNachDraussen(Basis):
    def test_next_auf_fremde_adresse_wird_verworfen(self):
        get_user_model().objects.create_superuser("chef", password="geheim123")
        antwort = self.client.post(
            reverse("termine:login") + "?next=https://boese.example.org/",
            {"username": "chef", "password": "geheim123"},
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertNotIn("boese.example.org", antwort["Location"])


class VoidAuthGruppenSynchronisationTest(TestCase):
    def test_buero_gruppe_setzt_is_staff(self):
        from unittest.mock import MagicMock

        from termine.social_adapter import VoidAuthSocialAccountAdapter

        user = get_user_model().objects.create_user("buero_user", email="buero@example.org")
        user.is_staff = False
        user.save()

        sociallogin = MagicMock()
        sociallogin.account.extra_data = {
            "groups": ["buero"],
            "email": "buero@example.org",
        }

        adapter = VoidAuthSocialAccountAdapter()
        adapter._sync_user_profile_and_groups(user, sociallogin)

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_fahrlehrer_gruppe_verknuepft_profil_ohne_staff_rechte(self):
        from unittest.mock import MagicMock

        from termine.social_adapter import VoidAuthSocialAccountAdapter

        lehrer = Fahrlehrer.objects.create(name="Max Mustermann", email="max@example.org")
        user = get_user_model().objects.create_user("max_user", first_name="Max", last_name="Mustermann", email="max@example.org")
        user.is_staff = True
        user.save()

        sociallogin = MagicMock()
        sociallogin.account.extra_data = {
            "groups": ["fahrlehrer"],
            "name": "Max Mustermann",
            "email": "max@example.org",
        }

        adapter = VoidAuthSocialAccountAdapter()
        adapter._sync_user_profile_and_groups(user, sociallogin)

        user.refresh_from_db()
        lehrer.refresh_from_db()

        # is_staff sollte auf False gesetzt werden
        self.assertFalse(user.is_staff)
        # Fahrlehrer sollte mit Benutzer verknüpft sein
        self.assertEqual(lehrer.benutzer, user)
        self.assertEqual(user.fahrlehrer, lehrer)

