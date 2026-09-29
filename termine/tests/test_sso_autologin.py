"""SSO-Auto-Redirect auf der Anmeldeseite samt Logout-Loop-Schutz.

Bei aktivem VoidAuth leitet die Anmeldeseite sofort in den OIDC-Flow um.
?sso=0 ist das Escape-Hatch (Passwort-Formular), der Logout hängt es
automatisch an — sonst würde die aktive VoidAuth-Session direkt wieder
einloggen und der Logout wäre nur ein Reload.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

SSO_LOGIN = "/accounts/oidc/voidauth/login/"

# VOIDAUTH ist in den Tests per Default aus — für die Redirect-Fälle gezielt
# einschalten, ohne dass eine echte Discovery beim Issuer nötig wäre (der
# Redirect entsteht vor jedem Kontakt zum Provider).
SSO_AN = override_settings(
    VOIDAUTH_ENABLED=True,
    SOCIALACCOUNT_PROVIDERS={
        "openid_connect": {
            "APPS": [
                {
                    "provider_id": "voidauth",
                    "name": "VoidAuth",
                    "client_id": "test",
                    "secret": "test",
                    "settings": {"server_url": "https://voidauth.example.org"},
                }
            ],
            "SCOPE": ["openid"],
        }
    },
)


@SSO_AN
class AutoRedirectTest(TestCase):
    def test_get_leitet_in_oidc_flow(self):
        antwort = self.client.get(reverse("termine:login"))
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(antwort["Location"], SSO_LOGIN)

    def test_next_wird_durchgereicht(self):
        antwort = self.client.get(reverse("termine:login") + "?next=/intern/planung/")
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(antwort["Location"], SSO_LOGIN + "?next=%2Fintern%2Fplanung%2F")

    def test_sso_gleich_null_zeigt_formular(self):
        antwort = self.client.get(reverse("termine:login") + "?sso=0")
        self.assertEqual(antwort.status_code, 200)
        self.assertTemplateUsed(antwort, "staff/anmelden.html")

    def test_angemeldeter_nutzer_wird_nicht_umgeleitet(self):
        get_user_model().objects.create_user("chef", password="geheim123")
        self.client.login(username="chef", password="geheim123")
        # Angemeldete sehen wie bisher das Formular (stock LoginView), der
        # Auto-Redirect greift nur für Gäste.
        antwort = self.client.get(reverse("termine:login"))
        self.assertEqual(antwort.status_code, 200)
        self.assertTemplateUsed(antwort, "staff/anmelden.html")

    def test_post_login_bleibt_lokal(self):
        get_user_model().objects.create_user("chef", password="geheim123")
        antwort = self.client.post(
            reverse("termine:login"), {"username": "chef", "password": "geheim123"}
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(antwort["Location"], "/intern/")

    def test_logout_endet_mit_sso_gleich_null(self):
        get_user_model().objects.create_user("chef", password="geheim123")
        self.client.login(username="chef", password="geheim123")
        antwort = self.client.post(reverse("termine:logout"))
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(antwort["Location"], reverse("termine:login") + "?sso=0")


class SSOAusTest(TestCase):
    """Ohne VOIDAUTH bleibt alles beim alten: Formular, kein Redirect."""

    def test_get_zeigt_formular(self):
        antwort = self.client.get(reverse("termine:login"))
        self.assertEqual(antwort.status_code, 200)
        self.assertTemplateUsed(antwort, "staff/anmelden.html")

    def test_next_wird_nicht_umgeleitet(self):
        antwort = self.client.get(reverse("termine:login") + "?next=/intern/")
        self.assertEqual(antwort.status_code, 200)

    def test_lokaler_login_funktioniert(self):
        get_user_model().objects.create_user("chef", password="geheim123")
        antwort = self.client.post(
            reverse("termine:login"), {"username": "chef", "password": "geheim123"}
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(antwort["Location"], "/intern/")
