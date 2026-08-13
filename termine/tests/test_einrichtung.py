"""Tests für den Hinweis auf die noch nicht eingerichtete Installation.

Der Hinweis ist die einzige Stelle, an der eine frische Installation erklärt,
warum nichts geht. Er muss deshalb zuverlässig erscheinen – und ebenso
zuverlässig wieder verschwinden, sobald es einen Superuser gibt. Besonders auf
der öffentlichen Seite: Ein Befehl fürs Terminal geht Interessenten nichts an.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models import Fahrlehrer, Termin, Terminart
from termine.services import einrichtung

BEFEHL = "manage.py createsuperuser"


class SuperuserErkennung(TestCase):
    def test_leere_installation_hat_keinen_superuser(self):
        self.assertTrue(einrichtung.superuser_fehlt())

    def test_ein_fahrlehrer_login_reicht_nicht(self):
        # Er darf die eigene Planung pflegen, aber keine Stammdaten anlegen.
        get_user_model().objects.create_user("anna", password="geheim123")
        self.assertTrue(einrichtung.superuser_fehlt())

    def test_mit_superuser_ist_alles_gut(self):
        get_user_model().objects.create_superuser("chef", password="geheim123")
        self.assertFalse(einrichtung.superuser_fehlt())
        self.assertIsNone(einrichtung.einrichtungshinweis())

    def test_datenbankfehler_loest_keinen_hinweis_aus(self):
        # Vor der ersten Migration gibt es die Tabelle noch nicht. Der Hinweis
        # ist dann nicht wichtig genug, um dafür eine Seite zu zerlegen.
        with mock.patch.object(
            einrichtung, "get_user_model", side_effect=DatabaseError("keine Tabelle")
        ):
            self.assertFalse(einrichtung.superuser_fehlt())


class BefehlPasstZurUmgebung(TestCase):
    def test_ohne_container_der_direkte_befehl(self):
        with mock.patch.object(einrichtung, "CONTAINER_MERKMAL", Path("/gibt-es-hier-nicht")):
            self.assertEqual(
                einrichtung.createsuperuser_befehl(), "python manage.py createsuperuser"
            )

    def test_im_container_ueber_docker_compose(self):
        # Irgendeine vorhandene Datei vertritt die Merkmalsdatei des Containers.
        with mock.patch.object(einrichtung, "CONTAINER_MERKMAL", Path(__file__)):
            befehl = einrichtung.createsuperuser_befehl()
        self.assertTrue(befehl.startswith("docker compose exec web "))
        self.assertIn(BEFEHL, befehl)


class HinweisAufDenSeiten(TestCase):
    def superuser_anlegen(self):
        return get_user_model().objects.create_superuser("chef", password="geheim123")

    def test_anmeldeseite_weist_auf_das_erste_konto_hin(self):
        antwort = self.client.get(reverse("termine:login"))
        self.assertContains(antwort, BEFEHL)

    def test_anmeldeseite_schweigt_mit_superuser(self):
        self.superuser_anlegen()
        antwort = self.client.get(reverse("termine:login"))
        self.assertNotContains(antwort, BEFEHL)

    def test_admin_anmeldung_weist_ebenfalls_hin(self):
        antwort = self.client.get(reverse("admin:login"))
        self.assertContains(antwort, BEFEHL)
        # Wer schon im Admin steht, braucht keinen Link dorthin.
        self.assertContains(antwort, "Danach melden Sie sich hier an")

    def test_admin_anmeldung_schweigt_mit_superuser(self):
        self.superuser_anlegen()
        antwort = self.client.get(reverse("admin:login"))
        self.assertNotContains(antwort, BEFEHL)

    def test_oeffentliche_seite_erklaert_die_leere_installation(self):
        antwort = self.client.get(reverse("termine:start"))
        self.assertContains(antwort, BEFEHL)

    def test_oeffentliche_seite_zeigt_besuchern_keine_befehle(self):
        # Sobald jemand die Installation betreut, ist der leere Kalender eine
        # Sache der Fahrschule und keine Nachricht an die Kundschaft.
        self.superuser_anlegen()
        antwort = self.client.get(reverse("termine:start"))
        self.assertNotContains(antwort, BEFEHL)

    def test_bei_gepflegten_stammdaten_kein_hinweis(self):
        art = Terminart.objects.create(name="Erstberatung", dauer_minuten=30)
        fahrlehrer = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )
        beginn = (timezone.now() + dt.timedelta(days=3)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        Termin.objects.create(
            fahrlehrer=fahrlehrer,
            terminart=art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )
        # Kein Superuser, aber Stammdaten sind da: Die Seite tut, was sie soll,
        # und fragt deshalb gar nicht erst nach dem Konto.
        with mock.patch.object(einrichtung, "superuser_fehlt") as gefragt:
            antwort = self.client.get(reverse("termine:start"))
        gefragt.assert_not_called()
        self.assertNotContains(antwort, BEFEHL)
