"""Tests für die Terminauswahl zum Einbetten in eine fremde Seite.

Der heikle Teil ist nicht die Darstellung, sondern die Freigabe: Eine Seite,
die sich von jedem in einen Rahmen setzen lässt, kann man unsichtbar über
fremde Schaltflächen legen. Deshalb steht hier vor allem, wer sie einrahmen
darf – und dass ohne ausdrückliche Erlaubnis niemand es darf.
"""

from __future__ import annotations

import datetime as dt

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from termine.models import Fahrlehrer, Termin, Terminart

SCHALTWERK = ["https://fahrschule-schaltwerk.de", "https://www.fahrschule-schaltwerk.de"]


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
        self.adresse = reverse("termine:einbetten")


class WerEinrahmenDarf(Basis):
    @override_settings(EMBED_ORIGINS=[])
    def test_ohne_eintrag_darf_niemand(self):
        antwort = self.client.get(self.adresse)
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.headers["Content-Security-Policy"], "frame-ancestors 'none'")
        # Das DENY der Middleware bleibt zusätzlich stehen.
        self.assertEqual(antwort.headers["X-Frame-Options"], "DENY")

    @override_settings(EMBED_ORIGINS=SCHALTWERK)
    def test_eingetragene_seiten_duerfen(self):
        antwort = self.client.get(self.adresse)
        self.assertEqual(
            antwort.headers["Content-Security-Policy"],
            "frame-ancestors https://fahrschule-schaltwerk.de https://www.fahrschule-schaltwerk.de",
        )

    @override_settings(EMBED_ORIGINS=SCHALTWERK)
    def test_das_deny_der_middleware_steht_der_freigabe_nicht_im_weg(self):
        # X-Frame-Options würde frame-ancestors überstimmen; die Ausnahme muss
        # also wirklich greifen und nicht nur gesetzt sein.
        antwort = self.client.get(self.adresse)
        self.assertNotIn("X-Frame-Options", antwort.headers)

    @override_settings(EMBED_ORIGINS=SCHALTWERK)
    def test_die_uebrigen_seiten_bleiben_gesperrt(self):
        # Die Freigabe gilt nur für die Auswahl, nicht für das Formular oder
        # den internen Bereich.
        for name in ("termine:start", "termine:login"):
            with self.subTest(seite=name):
                antwort = self.client.get(reverse(name))
                self.assertEqual(antwort.headers["X-Frame-Options"], "DENY")


class InhaltDerEinbettung(Basis):
    def test_zeigt_kalender_und_uhrzeiten(self):
        antwort = self.client.get(
            self.adresse, {"tag": self.termin.beginn_lokal.date().isoformat()}
        )
        self.assertContains(antwort, "buchungsbereich")
        self.assertContains(antwort, f"{self.termin.beginn_lokal:%H:%M}")

    def test_bringt_keine_zweite_kopfzeile_mit(self):
        # Die Einbettung sitzt mitten in einer fremden Seite; Marke, Navigation
        # und Fuß der App hätten dort nichts verloren.
        antwort = self.client.get(self.adresse)
        self.assertNotContains(antwort, "marke-zeichen")
        self.assertNotContains(antwort, 'class="fuss"')

    def test_uhrzeit_oeffnet_das_formular_ausserhalb_des_rahmens(self):
        # Das Buchungsformular braucht Cookies. In einem fremden Rahmen
        # blockieren Browser die zunehmend – deshalb verlässt der Schritt den
        # Rahmen.
        antwort = self.client.get(
            self.adresse, {"tag": self.termin.beginn_lokal.date().isoformat()}
        )
        self.assertContains(antwort, 'target="_blank"')
        self.assertContains(antwort, 'rel="noopener"')

    def test_blaettern_fuellt_nicht_die_chronik_der_fremden_seite(self):
        antwort = self.client.get(self.adresse)
        self.assertNotContains(antwort, "hx-push-url")

    def test_wird_nicht_von_suchmaschinen_erfasst(self):
        # Sonst stünde dieselbe Auswahl zweimal im Index.
        antwort = self.client.get(self.adresse)
        self.assertContains(antwort, 'name="robots" content="noindex"')

    def test_die_normale_startseite_blaettert_weiterhin_mit_chronik(self):
        antwort = self.client.get(reverse("termine:start"))
        self.assertContains(antwort, "hx-push-url")
