"""Was die Fahrschule früher nur im Django-Admin einstellen konnte.

Drei Dinge sind hier zusammengezogen, weil sie zusammen gehören: die
Terminarten, die eigenen Einstellungen samt Planungshorizont und die
Kleinigkeiten, die sonst niemand außer dem Admin konnte – Abo-Token,
Sperrzeiten, ein weiterer Fahrlehrer.
"""

from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models import (
    Buchung,
    Fahrlehrer,
    RhythmusRegel,
    Sperrzeit,
    Termin,
    Terminart,
)
from termine.services import buchung as buchungs_service


class Basis(TestCase):
    def setUp(self):
        self.art = Terminart.objects.create(name="Erstberatung", dauer_minuten=30)
        self.anna = Fahrlehrer.objects.create(
            name="Anna Berger",
            email="anna@example.org",
            bundesland="BW",
            vorlauf_stunden=0,
            horizont_wochen=4,
        )
        self.chef = get_user_model().objects.create_superuser("chef", password="geheim123")
        self.client.force_login(self.chef)

    def meldungen(self, antwort) -> list[str]:
        return [str(m) for m in get_messages(antwort.wsgi_request)]

    def termin_in(self, tagen: int) -> Termin:
        beginn = (timezone.now() + dt.timedelta(days=tagen)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        return Termin.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )


class TerminartenPflegen(Basis):
    def test_liste_zeigt_die_verwendung(self):
        self.termin_in(3)
        antwort = self.client.get(reverse("termine:terminarten"))
        self.assertEqual(antwort.status_code, 200)
        art = antwort.context["terminarten"][0]
        self.assertEqual(art.anzahl_termine, 1)
        self.assertEqual(art.anzahl_regeln, 0)

    def test_anlegen_erzeugt_das_kuerzel_aus_dem_namen(self):
        antwort = self.client.post(
            reverse("termine:terminart_neu"),
            {
                "name": "Beratung Klasse B",
                "dauer_minuten": 45,
                "puffer_minuten": 15,
                "beschreibung": "",
                "ort": "Büro",
                "fuehrerscheinklasse_abfragen": "on",
                "aktiv": "on",
                "reihenfolge": 0,
            },
        )
        self.assertRedirects(antwort, reverse("termine:terminarten"))
        neu = Terminart.objects.get(name="Beratung Klasse B")
        self.assertEqual(neu.slug, "beratung-klasse-b")
        self.assertEqual(neu.schrittweite_minuten, 60)

    def test_umbenennen_laesst_das_kuerzel_stehen(self):
        # Das Kürzel steckt in Links auf der Webseite der Fahrschule
        # (?art=erstberatung). Eine Umbenennung darf sie nicht zerreißen.
        self.client.post(
            reverse("termine:terminart_bearbeiten", args=[self.art.pk]),
            {
                "name": "Erstberatung (neu)",
                "dauer_minuten": 30,
                "puffer_minuten": 0,
                "beschreibung": "",
                "ort": "",
                "aktiv": "on",
                "reihenfolge": 0,
            },
        )
        self.art.refresh_from_db()
        self.assertEqual(self.art.name, "Erstberatung (neu)")
        self.assertEqual(self.art.slug, "erstberatung")

    def test_gleicher_name_wird_abgelehnt(self):
        antwort = self.client.post(
            reverse("termine:terminart_neu"),
            {
                "name": "erstberatung",
                "dauer_minuten": 30,
                "puffer_minuten": 0,
                "beschreibung": "",
                "ort": "",
                "aktiv": "on",
                "reihenfolge": 0,
            },
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("bereits", str(antwort.context["form"].errors))
        self.assertEqual(Terminart.objects.count(), 1)

    def test_unbenutzte_terminart_laesst_sich_loeschen(self):
        frei = Terminart.objects.create(name="Probestunde", dauer_minuten=20)
        antwort = self.client.post(reverse("termine:terminart_loeschen", args=[frei.pk]))
        self.assertRedirects(antwort, reverse("termine:terminarten"))
        self.assertFalse(Terminart.objects.filter(pk=frei.pk).exists())

    def test_benutzte_terminart_bleibt_stehen(self):
        # PROTECT auf Termin.terminart: Ein Löschversuch endete sonst im
        # ProtectedError – und eine gebuchte Beratung verlöre ihre Bezeichnung.
        self.termin_in(3)
        antwort = self.client.post(reverse("termine:terminart_loeschen", args=[self.art.pk]))
        self.assertRedirects(
            antwort, reverse("termine:terminart_bearbeiten", args=[self.art.pk])
        )
        self.assertTrue(Terminart.objects.filter(pk=self.art.pk).exists())
        self.assertIn("wird noch verwendet", " ".join(self.meldungen(antwort)))

    def test_das_formular_erklaert_die_verwendung_statt_zu_loeschen(self):
        self.termin_in(3)
        antwort = self.client.get(
            reverse("termine:terminart_bearbeiten", args=[self.art.pk])
        )
        self.assertEqual(antwort.context["verwendung"], {"termine": 1, "regeln": 0})
        self.assertNotContains(
            antwort, reverse("termine:terminart_loeschen", args=[self.art.pk])
        )

    def test_auch_eine_regel_haelt_die_terminart_fest(self):
        RhythmusRegel.objects.create(
            fahrlehrer=self.anna, terminart=self.art, wochentage=[0], beginn=dt.time(9, 0),
            ende=dt.time(12, 0),
        )
        self.client.post(reverse("termine:terminart_loeschen", args=[self.art.pk]))
        self.assertTrue(Terminart.objects.filter(pk=self.art.pk).exists())

    def test_deaktivierte_terminart_verschwindet_aus_dem_angebot(self):
        termin = self.termin_in(3)
        self.assertIn(termin, Termin.objects.buchbar())

        self.client.post(
            reverse("termine:terminart_bearbeiten", args=[self.art.pk]),
            {
                "name": "Erstberatung",
                "dauer_minuten": 30,
                "puffer_minuten": 0,
                "beschreibung": "",
                "ort": "",
                "reihenfolge": 0,
            },
        )
        self.art.refresh_from_db()
        self.assertFalse(self.art.aktiv)
        self.assertNotIn(termin, Termin.objects.buchbar())


class BuchungshorizontEinstellen(Basis):
    def test_die_seite_nennt_den_letzten_buchbaren_tag(self):
        antwort = self.client.get(reverse("termine:einstellungen"))
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(
            antwort.context["buchbar_bis"],
            timezone.localdate() + dt.timedelta(days=4 * 7 - 1),
        )

    def test_termin_hinter_dem_horizont_wird_nicht_angeboten(self):
        drinnen = self.termin_in(7)
        draussen = self.termin_in(40)  # 40 Tage > 4 Wochen
        buchbar = list(Termin.objects.buchbar())
        self.assertIn(drinnen, buchbar)
        self.assertNotIn(draussen, buchbar)

    def test_hoeherer_horizont_macht_den_termin_buchbar(self):
        from termine.models import FahrschulEinstellungen

        draussen = self.termin_in(40)

        antwort = self.client.post(
            f"{reverse('termine:einstellungen')}?fahrlehrer={self.anna.slug}",
            {
                "form_art": "global",
                "vorlauf_stunden": 0,
                "horizont_wochen": 8,
            },
        )

        self.assertEqual(antwort.status_code, 302)
        einst = FahrschulEinstellungen.get_solo()
        self.assertEqual(einst.horizont_wochen, 8)
        self.assertIn(draussen, Termin.objects.buchbar())

    def test_verkuerzen_meldet_die_termine_dahinter(self):
        from termine.models import FahrschulEinstellungen

        self.termin_in(20)
        antwort = self.client.post(
            f"{reverse('termine:einstellungen')}?fahrlehrer={self.anna.slug}",
            {
                "form_art": "global",
                "vorlauf_stunden": 0,
                "horizont_wochen": 2,
            },
        )
        self.assertEqual(antwort.status_code, 302)
        einst = FahrschulEinstellungen.get_solo()
        self.assertEqual(einst.horizont_wochen, 2)
        self.assertEqual(Termin.objects.count(), 1)
        self.assertEqual(Termin.objects.buchbar().count(), 0)

    def test_das_buchungsformular_lehnt_einen_termin_dahinter_ab(self):
        draussen = self.termin_in(40)
        self.client.logout()
        antwort = self.client.get(reverse("termine:buchen", args=[draussen.pk]))
        self.assertEqual(antwort.status_code, 409)

    def test_auch_der_dienst_laesst_sich_nicht_ueberreden(self):
        draussen = self.termin_in(40)
        with self.assertRaises(buchungs_service.TerminNichtVerfuegbar):
            buchungs_service.reservieren(
                draussen.pk, name="Lena", email="lena@example.org"
            )
        self.assertFalse(Buchung.objects.exists())

    def test_verfuegbare_fuehrerscheinklassen_filtern(self):
        from termine.forms import BuchungsForm
        from termine.models import FahrschulEinstellungen

        # Speichern von aktiven Klassen: nur B und B197
        antwort = self.client.post(
            f"{reverse('termine:einstellungen')}?fahrlehrer={self.anna.slug}",
            {
                "form_art": "global",
                "vorlauf_stunden": 24,
                "horizont_wochen": 4,
                "bundesland": "BE",
                "aktive_fuehrerscheinklassen": ["B", "B197"],
            },
        )
        self.assertEqual(antwort.status_code, 302)
        einst = FahrschulEinstellungen.get_solo()
        self.assertEqual(einst.aktive_fuehrerscheinklassen, ["B", "B197"])

        form = BuchungsForm()
        choice_keys = [c[0] for c in form.fields["fuehrerscheinklasse"].choices]
        self.assertEqual(choice_keys, ["", "B", "B197"])


class EigeneEinstellungen(Basis):
    def test_fahrlehrer_darf_die_eigenen_daten_aendern(self):
        benutzer = get_user_model().objects.create_user("anna", password="geheim123")
        self.anna.benutzer = benutzer
        self.anna.save(update_fields=["benutzer"])
        self.client.force_login(benutzer)

        antwort = self.client.post(
            reverse("termine:einstellungen"),
            {
                "name": "Anna Berger",
                "email": "neu@example.org",
                "telefon": "0176 1234567",
                "beschreibung": "Beratung nach Vereinbarung.",
                "bundesland": "BY",
            },
        )

        self.assertEqual(antwort.status_code, 302)
        self.anna.refresh_from_db()
        self.assertEqual(self.anna.email, "neu@example.org")
        self.assertEqual(self.anna.bundesland, "BY")
        self.assertEqual(self.anna.telefon, "0176 1234567")

    def test_aktiv_und_reihenfolge_sieht_nur_der_inhaber(self):
        benutzer = get_user_model().objects.create_user("anna", password="geheim123")
        self.anna.benutzer = benutzer
        self.anna.save(update_fields=["benutzer"])
        self.client.force_login(benutzer)

        felder = self.client.get(reverse("termine:einstellungen")).context["form"].fields
        self.assertNotIn("aktiv", felder)
        self.assertNotIn("reihenfolge", felder)

        self.client.force_login(self.chef)
        felder = self.client.get(reverse("termine:einstellungen")).context["form"].fields
        self.assertIn("aktiv", felder)

    def test_ein_deaktivierter_fahrlehrer_bleibt_erreichbar(self):
        # Sonst wäre der Haken bei „Aktiv" eine Einbahnstraße: Der tägliche
        # Betrieb blendet Inaktive aus, und niemand käme je wieder heran.
        self.anna.aktiv = False
        self.anna.save(update_fields=["aktiv"])

        antwort = self.client.get(
            f"{reverse('termine:einstellungen')}?fahrlehrer={self.anna.slug}"
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.context["fahrlehrer"], self.anna)

    def test_ohne_fahrlehrer_bietet_die_seite_das_anlegen_an(self):
        self.anna.delete()
        antwort = self.client.get(reverse("termine:einstellungen"))
        self.assertEqual(antwort.status_code, 200)
        self.assertIsNone(antwort.context["fahrlehrer"])
        self.assertContains(antwort, reverse("termine:fahrlehrer_neu"))


class KalenderAboUndSperrzeiten(Basis):
    def test_neues_abo_macht_das_alte_ungueltig(self):
        alt = self.anna.feed_token
        self.assertEqual(self.client.get(f"/kalender/{alt}.ics").status_code, 200)

        antwort = self.client.post(
            f"{reverse('termine:feed_token_neu')}?fahrlehrer={self.anna.slug}"
        )

        self.assertEqual(antwort.status_code, 302)
        self.anna.refresh_from_db()
        self.assertNotEqual(self.anna.feed_token, alt)
        self.assertEqual(self.client.get(f"/kalender/{alt}.ics").status_code, 404)
        self.assertEqual(
            self.client.get(f"/kalender/{self.anna.feed_token}.ics").status_code, 200
        )

    def test_sperrzeit_laesst_sich_wieder_aufheben(self):
        jetzt = timezone.now()
        sperre = Sperrzeit.objects.create(
            fahrlehrer=self.anna,
            beginn=jetzt + dt.timedelta(days=1),
            ende=jetzt + dt.timedelta(days=3),
            grund="Urlaub",
        )
        antwort = self.client.post(reverse("termine:sperrzeit_loeschen", args=[sperre.pk]))
        self.assertEqual(antwort.status_code, 302)
        self.assertFalse(Sperrzeit.objects.filter(pk=sperre.pk).exists())

    def test_abo_zuruecksetzen_ohne_fahrlehrer_meldet_das(self):
        self.anna.delete()
        antwort = self.client.post(reverse("termine:feed_token_neu"))
        self.assertRedirects(antwort, reverse("termine:einstellungen"))
        self.assertIn("Kein Fahrlehrer ausgewählt.", self.meldungen(antwort))

    def test_die_tagesplanung_bietet_das_aufheben_gleich_mit_an(self):
        # Eingetragen wird die Sperrzeit dort; wer sie zu früh beendet, soll
        # sie nicht erst in den Einstellungen suchen müssen.
        jetzt = timezone.now()
        sperre = Sperrzeit.objects.create(
            fahrlehrer=self.anna, beginn=jetzt, ende=jetzt + dt.timedelta(days=1)
        )
        antwort = self.client.get(reverse("termine:tagesplanung"))
        self.assertContains(
            antwort, reverse("termine:sperrzeit_loeschen", args=[sperre.pk])
        )

    def test_die_seite_zeigt_nur_kommende_sperrzeiten(self):
        jetzt = timezone.now()
        Sperrzeit.objects.create(
            fahrlehrer=self.anna,
            beginn=jetzt - dt.timedelta(days=10),
            ende=jetzt - dt.timedelta(days=8),
            grund="Vorbei",
        )
        kommend = Sperrzeit.objects.create(
            fahrlehrer=self.anna,
            beginn=jetzt + dt.timedelta(days=1),
            ende=jetzt + dt.timedelta(days=2),
            grund="Urlaub",
        )
        antwort = self.client.get(reverse("termine:einstellungen"))
        self.assertEqual(list(antwort.context["sperrzeiten"]), [kommend])


class FahrlehrerAnlegen(Basis):
    def test_das_formular_steht_dem_inhaber_offen(self):
        antwort = self.client.get(reverse("termine:fahrlehrer_neu"))
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("name", antwort.context["form"].fields)

    def test_der_inhaber_legt_ohne_admin_einen_zweiten_an(self):
        antwort = self.client.post(
            reverse("termine:fahrlehrer_neu"),
            {
                "name": "Tom Kern",
                "email": "tom@example.org",
                "telefon": "",
                "beschreibung": "",
                "bundesland": "NW",
                "aktiv": "on",
                "reihenfolge": 1,
            },
        )
        self.assertEqual(antwort.status_code, 302)
        tom = Fahrlehrer.objects.get(name="Tom Kern")
        self.assertEqual(tom.slug, "tom-kern")
        self.assertEqual(tom.bundesland, "NW")
        # Ein eigenes Abo bekommt er sofort, ein Login nicht.
        self.assertTrue(tom.feed_token)
        self.assertIsNone(tom.benutzer)

    def test_unvollstaendige_angaben_legen_niemanden_an(self):
        antwort = self.client.post(
            reverse("termine:fahrlehrer_neu"),
            {"name": "", "email": "keine-adresse", "bundesland": "BW", "reihenfolge": 0},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("name", antwort.context["form"].errors)
        self.assertIn("email", antwort.context["form"].errors)
        self.assertEqual(Fahrlehrer.objects.count(), 1)
