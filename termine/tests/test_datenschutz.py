"""Tests für das Löschen auf Kundenwunsch und die Fehlerseiten.

Das Löschen hängt am selben Nachweis wie das Ansehen und das Absagen: am
Zugriffs-Token aus der Bestätigungsmail. Wer fremde Daten löschen wollte,
müsste sie erst sehen können – und dafür 256 Bit Zufall erraten. Diese Tests
halten fest, dass es keinen zweiten Weg gibt.
"""

from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.core import mail as django_mail
from django.template.loader import get_template
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models import Buchung, Fahrlehrer, Termin, Terminart
from termine.services import buchung as buchungs_service


class Basis(TestCase):
    def setUp(self):
        self.art = Terminart.objects.create(name="Erstberatung", dauer_minuten=30)
        self.anna = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )

    def buchung_anlegen(self, *, tage_voraus: int = 5, bestaetigt: bool = True) -> Buchung:
        beginn = (timezone.now() + dt.timedelta(days=tage_voraus)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        termin = Termin.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )
        buchung = buchungs_service.reservieren(
            termin.pk,
            name="Lena Hoffmann",
            email="lena@example.org",
            telefon="0176 1234567",
            nachricht="Bitte melden Sie sich vorher kurz.",
        )
        if bestaetigt:
            buchung = buchungs_service.bestaetigen(buchung)
        django_mail.outbox.clear()
        return buchung


class LoeschenAufWunsch(Basis):
    def test_ueberschreibt_die_personenbezogenen_felder(self):
        buchung = self.buchung_anlegen()

        antwort = self.client.post(
            reverse("termine:buchung_loeschen", args=[buchung.token])
        )

        self.assertEqual(antwort.status_code, 200)
        buchung.refresh_from_db()
        self.assertEqual(buchung.name, "Gelöscht")
        self.assertEqual(buchung.email, "")
        self.assertEqual(buchung.telefon, "")
        self.assertEqual(buchung.nachricht, "")
        self.assertIsNotNone(buchung.anonymisiert_am)

    def test_der_termin_wird_wieder_freigegeben(self):
        buchung = self.buchung_anlegen()
        termin = buchung.termin

        self.client.post(reverse("termine:buchung_loeschen", args=[buchung.token]))

        termin.refresh_from_db()
        buchung.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.FREI)
        self.assertEqual(buchung.status, Buchung.Status.STORNIERT)

    def test_der_kunde_wird_darueber_informiert(self):
        buchung = self.buchung_anlegen()

        with self.captureOnCommitCallbacks(execute=True):
            antwort = self.client.post(
                reverse("termine:buchung_loeschen", args=[buchung.token])
            )

        # Auf der Seite …
        self.assertContains(antwort, "abgesagt")
        # … und per Mail, solange die Adresse noch da war.
        empfaenger = [adresse for m in django_mail.outbox for adresse in m.to]
        self.assertIn("lena@example.org", empfaenger)
        self.assertIn(self.anna.email, empfaenger)

    def test_der_hinweis_steht_schon_vor_dem_klick_auf_der_seite(self):
        buchung = self.buchung_anlegen()
        antwort = self.client.get(reverse("termine:buchung", args=[buchung.token]))
        self.assertContains(antwort, "Damit sagen Sie auch Ihren Termin ab")

    def test_der_link_traegt_danach_nicht_mehr(self):
        buchung = self.buchung_anlegen()
        self.client.post(reverse("termine:buchung_loeschen", args=[buchung.token]))

        for name in ("termine:buchung", "termine:bestaetigen"):
            with self.subTest(seite=name):
                antwort = self.client.get(reverse(name, args=[buchung.token]))
                self.assertEqual(antwort.status_code, 404)

    def test_ein_vergangener_termin_wird_nicht_mehr_freigegeben(self):
        buchung = self.buchung_anlegen()
        termin = buchung.termin
        # Erst buchen, dann in die Vergangenheit schieben: anders herum lässt
        # die App es zu Recht gar nicht zu.
        Termin.objects.filter(pk=termin.pk).update(
            beginn=timezone.now() - dt.timedelta(days=5),
            ende=timezone.now() - dt.timedelta(days=5) + dt.timedelta(minutes=30),
        )

        self.client.post(reverse("termine:buchung_loeschen", args=[buchung.token]))

        termin.refresh_from_db()
        buchung.refresh_from_db()
        # Der Termin bleibt gebucht – rückwirkend absagen wäre eine Fälschung
        # der Historie.
        self.assertEqual(termin.status, Termin.Status.GEBUCHT)
        self.assertEqual(buchung.name, "Gelöscht")


class NurMitDemEigenenToken(Basis):
    def test_ein_fremder_token_findet_nichts(self):
        self.buchung_anlegen()
        antwort = self.client.post(reverse("termine:buchung_loeschen", args=["a" * 43]))
        self.assertEqual(antwort.status_code, 404)

    def test_ein_anderer_kunde_kommt_an_die_fremde_buchung_nicht_heran(self):
        eine = self.buchung_anlegen()
        andere = self.buchung_anlegen(tage_voraus=6)

        # Mit dem eigenen Token wird nur die eigene Buchung gelöscht.
        self.client.post(reverse("termine:buchung_loeschen", args=[andere.token]))

        eine.refresh_from_db()
        andere.refresh_from_db()
        self.assertEqual(eine.name, "Lena Hoffmann")
        self.assertEqual(andere.name, "Gelöscht")

    def test_ein_aufruf_ohne_absenden_loescht_nichts(self):
        buchung = self.buchung_anlegen()
        antwort = self.client.get(reverse("termine:buchung_loeschen", args=[buchung.token]))
        self.assertEqual(antwort.status_code, 405)
        buchung.refresh_from_db()
        self.assertEqual(buchung.name, "Lena Hoffmann")


class Fehlerseiten(Basis):
    """Die Seiten müssen ohne Datenbank und ohne Kontext bestehen können."""

    def test_unbekannte_adresse_zeigt_die_eigene_404(self):
        antwort = self.client.get("/gibt-es-nicht/")
        self.assertEqual(antwort.status_code, 404)
        self.assertContains(antwort, "Diese Seite gibt es nicht", status_code=404)
        self.assertContains(antwort, "Zur Terminauswahl", status_code=404)

    def test_fehlende_berechtigung_zeigt_die_eigene_403(self):
        benutzer = get_user_model().objects.create_user("ohne", password="geheim123")
        self.client.force_login(benutzer)
        antwort = self.client.get(reverse("termine:dashboard"))
        self.assertEqual(antwort.status_code, 403)
        self.assertContains(antwort, "Berechtigung", status_code=403)
        # Der Text schickt zur Anmeldung – dann muss der Weg dorthin auch da
        # sein. Im Seitenfuß steht er, aber nicht im Kopf.
        self.assertContains(antwort, reverse("termine:login"), status_code=403)

    def test_jede_fehlerseite_hat_eine_eigene_ueberschrift_im_reiter(self):
        # Überschrift und Reiter sind zwei getrennte Blöcke; ein leerer Reiter
        # fällt beim Ansehen nicht auf, im Verlauf des Browsers dagegen schon.
        faelle = [
            ("/gibt-es-nicht/", 404, "Diese Seite gibt es nicht"),
            ("/", 400, "Die Anfrage war unverständlich"),
        ]
        for pfad, code, erwartet in faelle:
            with self.subTest(seite=code):
                kopf = {"host": "boese.example.org"} if code == 400 else {}
                antwort = self.client.get(pfad, headers=kopf)
                self.assertEqual(antwort.status_code, code)
                self.assertContains(
                    antwort, f"<title>{erwartet} – ", status_code=code
                )

    def test_falscher_hostname_zeigt_die_eigene_400(self):
        antwort = self.client.get("/", headers={"host": "boese.example.org"})
        self.assertEqual(antwort.status_code, 400)
        self.assertContains(antwort, "unverständlich", status_code=400)

    def test_die_500_kommt_ohne_kontext_und_ohne_datenbank_aus(self):
        # Django rendert sie ohne Request. Wer dort {{ SITE_NAME }} oder ein
        # {% url %} auf eine Datenbankabfrage baut, bekommt im Ernstfall einen
        # Fehler beim Anzeigen des Fehlers.
        inhalt = get_template("500.html").render()
        self.assertIn("Kurz gestolpert", inhalt)
        self.assertIn('href="/"', inhalt)
