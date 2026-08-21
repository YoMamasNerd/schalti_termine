"""Tests für die Markierung des aktuellen Navigationspunkts.

Die Zuordnung „welche Adresse gehört zu welchem Punkt" steht in der Vorlage
und ist damit leicht zu übersehen, wenn eine neue Seite dazukommt. Diese Tests
halten sie fest – besonders die Unterseiten, die zu einem Punkt gehören, ohne
seine eigene Adresse zu sein.
"""

from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models import Fahrlehrer, RhythmusRegel, Terminart
from termine.templatetags.navigation import aktiv


class AktiverPunkt(TestCase):
    def setUp(self):
        self.chef = get_user_model().objects.create_superuser("chef", password="geheim123")
        self.client.force_login(self.chef)
        self.art = Terminart.objects.create(name="Erstberatung", dauer_minuten=30)
        self.anna = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW"
        )

    def punkte(self, antwort) -> list[str]:
        """Die Beschriftungen aller als aktiv markierten Punkte."""
        html = antwort.content.decode()
        gefunden = []
        for stueck in html.split('class="aktiv"')[1:]:
            gefunden.append(stueck.split(">", 1)[1].split("<", 1)[0].strip())
        return gefunden

    def test_jede_hauptseite_markiert_genau_sich_selbst(self):
        for name, beschriftung in (
            ("termine:dashboard", "Übersicht"),
            ("termine:tagesplanung", "Tagesplanung"),
            ("termine:regeln", "Rhythmus-Regeln"),
            ("termine:buchungen", "Buchungen"),
        ):
            with self.subTest(seite=name):
                antwort = self.client.get(reverse(name))
                self.assertEqual(self.punkte(antwort), [beschriftung])

    def test_unterseite_markiert_ihren_bereich(self):
        # Das Regelformular ist eine eigene Adresse, gehört aber zu den Regeln.
        regel = RhythmusRegel.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            wochentage=[1],
            beginn=dt.time(14, 0),
            ende=dt.time(17, 0),
            gueltig_ab=timezone.localdate(),
        )
        antwort = self.client.get(reverse("termine:regel_bearbeiten", args=[regel.pk]))
        self.assertEqual(self.punkte(antwort), ["Rhythmus-Regeln"])

    def test_aktiver_punkt_ist_auch_fuer_screenreader_erkennbar(self):
        antwort = self.client.get(reverse("termine:buchungen"))
        self.assertContains(antwort, 'aria-current="page"', count=1)

    def test_oeffentliche_seite_hat_keine_interne_navigation_fuer_nicht_angemeldete(self):
        self.client.logout()
        antwort = self.client.get(reverse("termine:start"))
        self.assertNotContains(antwort, "menue-schalter")

    def test_oeffentliche_seite_zeigt_interne_navigation_fuer_angemeldete_mitarbeiter(self):
        self.client.force_login(self.chef)
        antwort = self.client.get(reverse("termine:start"))
        self.assertContains(antwort, "menue-schalter")
        self.assertContains(antwort, "Interner Bereich")


class OhneRequest(SimpleTestCase):
    def test_kein_request_im_kontext_markiert_nichts(self):
        # Fehlerseiten werden ohne den üblichen Kontext gerendert. Der Tag darf
        # dort nichts markieren, aber erst recht nicht scheitern.
        self.assertEqual(aktiv({}, "dashboard"), "")


class KoerperKlasse(TestCase):
    """Öffentlich und intern sind optisch getrennt – das hängt an dieser Klasse."""

    def test_oeffentliche_seite_ist_als_solche_ausgezeichnet(self):
        antwort = self.client.get(reverse("termine:start"))
        self.assertContains(antwort, '<body class="oeffentlich">')

    def test_interner_bereich_verzichtet_auf_die_empfangsgesten(self):
        chef = get_user_model().objects.create_superuser("chef2", password="geheim123")
        self.client.force_login(chef)
        antwort = self.client.get(reverse("termine:dashboard"))
        self.assertContains(antwort, '<body class="intern">')


class StammdatenSichtbarkeit(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser("chef", password="geheim123")
        self.staff_user = get_user_model().objects.create_user("mitarbeiter", password="geheim123", is_staff=True)
        Fahrlehrer.objects.create(name="Mitarbeiter", benutzer=self.staff_user)

    def test_stammdaten_fuer_superuser_sichtbar(self):
        self.client.force_login(self.superuser)
        antwort = self.client.get(reverse("termine:dashboard"))
        self.assertContains(antwort, "Stammdaten")

    def test_stammdaten_fuer_normalen_staff_unsichtbar(self):
        self.client.force_login(self.staff_user)
        antwort = self.client.get(reverse("termine:dashboard"))
        self.assertNotContains(antwort, "Stammdaten")
