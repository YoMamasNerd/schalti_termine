"""Der interne Bereich darf sich niemandem zeigen, der nicht angemeldet ist.

Der Test zählt keine Adressen auf, sondern geht die URL-Konfiguration durch.
Das ist Absicht: Eine Aufzählung altert – wer eine neue interne Seite
hinzufügt und den Test nicht kennt, hätte sonst eine ungeprüfte Seite. So
fällt jede neue Adresse unter `intern/` von selbst in die Prüfung.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import get_resolver, reverse

from termine import staff_views
from termine.models import Fahrlehrer, Sperrzeit

# Diese beiden gehören zum internen Bereich, sind aber notwendigerweise
# öffentlich – ohne sie käme niemand hinein oder hinaus.
OHNE_LOGIN_ERLAUBT = {"login", "logout"}

# Platzhalter für die Adressbausteine. Die Werte müssen zu nichts Echtem
# gehören: Wer nicht angemeldet ist, darf nicht einmal erfahren, ob es das
# Angefragte gibt.
BEISPIELE = {
    "int": "1",
    "str": "x" * 43,
    "slug": "beispiel",
}


def _beispieladresse(muster: str) -> str:
    """Ersetzt <int:pk> und Verwandte durch einen brauchbaren Beispielwert."""

    def ersatz(treffer: re.Match) -> str:
        art = treffer.group(1) or "str"
        return BEISPIELE[art]

    return "/" + re.sub(r"<(?:(\w+):)?\w+>", ersatz, muster)


def interne_adressen() -> list[tuple[str, str]]:
    """Alle Adressen unterhalb von /intern/ – als (Name, Beispieladresse)."""
    gefunden = []
    for muster in get_resolver().url_patterns:
        for eintrag in getattr(muster, "url_patterns", [muster]):
            pfad = str(eintrag.pattern)
            if not pfad.startswith("intern/"):
                continue
            if eintrag.name in OHNE_LOGIN_ERLAUBT:
                continue
            gefunden.append((eintrag.name, _beispieladresse(pfad)))
    return gefunden


class OhneAnmeldungKeinInternerBereich(TestCase):
    def test_es_gibt_ueberhaupt_interne_adressen(self):
        # Sonst liefe die Schleife unten ins Leere und der Test wäre still grün.
        self.assertGreater(len(interne_adressen()), 5)

    def test_jede_interne_adresse_leitet_zur_anmeldung(self):
        for name, adresse in interne_adressen():
            for methode in ("get", "post"):
                with self.subTest(name=name, methode=methode):
                    antwort = getattr(self.client, methode)(adresse)
                    self.assertEqual(antwort.status_code, 302, adresse)
                    self.assertTrue(
                        antwort["Location"].startswith("/intern/anmelden/"),
                        f"{adresse} leitet nach {antwort['Location']}",
                    )

    def test_angemeldet_ohne_fahrlehrer_und_ohne_staff_bleibt_draussen(self):
        get_user_model().objects.create_user("kunde", password="geheim123")
        self.client.login(username="kunde", password="geheim123")
        for name, adresse in interne_adressen():
            with self.subTest(name=name):
                self.assertEqual(self.client.get(adresse).status_code, 403, adresse)


class KeineInterneSeiteAusserhalbVonIntern(TestCase):
    """Der Sammeltest oben erkennt interne Seiten am Adresspfad.

    Diese Prüfung hält die Annahme dahinter aufrecht: Wer eine Ansicht in
    `staff_views` anlegt und sie versehentlich unter einer öffentlichen
    Adresse einhängt, fiele sonst still aus der Prüfung heraus.
    """

    def test_alle_ansichten_aus_staff_views_liegen_unter_intern(self):
        for muster in get_resolver().url_patterns:
            for eintrag in getattr(muster, "url_patterns", [muster]):
                ansicht = getattr(eintrag, "callback", None)
                if getattr(ansicht, "__module__", "") != staff_views.__name__:
                    continue
                with self.subTest(name=eintrag.name):
                    self.assertTrue(
                        str(eintrag.pattern).startswith("intern/"),
                        f"{eintrag.name} liegt unter /{eintrag.pattern}",
                    )


class FahrlehrerSiehtNurSichSelbst(TestCase):
    """Die zweite Grenze: angemeldet, aber nicht für fremde Daten zuständig."""

    def setUp(self):
        benutzer = get_user_model().objects.create_user("anna", password="geheim123")
        self.anna = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", benutzer=benutzer
        )
        self.tom = Fahrlehrer.objects.create(name="Tom Kern", email="tom@example.org")
        self.client.login(username="anna", password="geheim123")

    def test_einstellungen_zeigen_den_eigenen_eintrag_auch_bei_fremdem_slug(self):
        antwort = self.client.get(
            f"{reverse('termine:einstellungen')}?fahrlehrer={self.tom.slug}"
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.context["fahrlehrer"], self.anna)

    def test_fremde_sperrzeit_liefert_404(self):
        from django.utils import timezone
        import datetime as dt

        jetzt = timezone.now()
        fremd = Sperrzeit.objects.create(
            fahrlehrer=self.tom, beginn=jetzt, ende=jetzt + dt.timedelta(days=1)
        )
        antwort = self.client.post(
            reverse("termine:sperrzeit_loeschen", args=[fremd.pk])
        )
        # 404 und nicht 403: Dass es diese Sperrzeit gibt, geht Anna nichts an.
        self.assertEqual(antwort.status_code, 404)
        self.assertTrue(Sperrzeit.objects.filter(pk=fremd.pk).exists())

    def test_fahrlehrer_anlegen_bleibt_dem_inhaber_vorbehalten(self):
        self.assertEqual(self.client.get(reverse("termine:fahrlehrer_neu")).status_code, 403)
        antwort = self.client.post(
            reverse("termine:fahrlehrer_neu"),
            {"name": "Heimlich", "email": "x@example.org", "bundesland": "BW",
             "vorlauf_stunden": 24, "horizont_wochen": 4, "reihenfolge": 0},
        )
        self.assertEqual(antwort.status_code, 403)
        self.assertFalse(Fahrlehrer.objects.filter(name="Heimlich").exists())

    def test_terminarten_darf_ein_fahrlehrer_dagegen_pflegen(self):
        # Terminarten sind Stammdaten der Fahrschule, keine persönlichen Daten.
        # Genau das war der Anlass, sie aus dem Admin zu holen.
        self.assertEqual(self.client.get(reverse("termine:terminarten")).status_code, 200)
        self.assertEqual(self.client.get(reverse("termine:terminart_neu")).status_code, 200)
