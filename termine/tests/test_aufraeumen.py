"""Tests für das Entfernen von Terminen, die eine Buchungshistorie tragen.

Der Ausgangsfehler: `Buchung.termin` steht auf PROTECT, damit ein Aufräumlauf
keinen Beleg wegwirft. Genau das ließ aber jeden Löschpfad mit einem
ProtectedError enden, sobald ein Termin einmal gebucht und wieder storniert
worden war – und das ist bei einer Buchung mit Selbst-Storno der Normalfall,
kein Sonderfall.

Am gefährlichsten war es im Generator: Der läuft nachts unbeaufsichtigt. Ein
Absturz dort heißt, dass keine neuen Termine mehr entstehen – ohne dass
irgendetwas rot leuchtet.
"""

from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models import Buchung, Fahrlehrer, RhythmusRegel, Sperrzeit, Termin, Terminart
from termine.services import buchung as buchungs_service
from termine.services.planung import generiere_termine, termine_entfernen


class Basis(TestCase):
    def setUp(self):
        self.art = Terminart.objects.create(name="Erstberatung", dauer_minuten=30)
        self.anna = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )
        self.chef = get_user_model().objects.create_superuser("chef", password="geheim123")

    def termin_am(self, tage_voraus: int = 5, stunde: int = 10) -> Termin:
        beginn = (timezone.now() + dt.timedelta(days=tage_voraus)).replace(
            hour=stunde, minute=0, second=0, microsecond=0
        )
        return Termin.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )

    def mit_storno_historie(self, **kwargs) -> Termin:
        """Ein wieder freier Termin, an dem eine stornierte Buchung hängt."""
        termin = self.termin_am(**kwargs)
        buchung = buchungs_service.reservieren(
            termin.pk, name="Lena Hoffmann", email="lena@example.org"
        )
        buchungs_service.bestaetigen(buchung)
        buchungs_service.stornieren(buchung, benachrichtigen=False)
        termin.refresh_from_db()
        assert termin.status == Termin.Status.FREI
        return termin


class Entfernen(Basis):
    def test_termin_ohne_historie_wird_geloescht(self):
        termin = self.termin_am()
        geloescht, entfallen = termine_entfernen(Termin.objects.filter(pk=termin.pk))
        self.assertEqual((geloescht, entfallen), (1, set()))
        self.assertFalse(Termin.objects.filter(pk=termin.pk).exists())

    def test_termin_mit_historie_bleibt_als_entfallen_stehen(self):
        termin = self.mit_storno_historie()
        geloescht, entfallen = termine_entfernen(Termin.objects.filter(pk=termin.pk))

        self.assertEqual(geloescht, 0)
        self.assertEqual(entfallen, {termin.pk})
        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.ENTFALLEN)
        # Der Beleg ist unangetastet.
        self.assertEqual(Buchung.objects.filter(termin=termin).count(), 1)

    def test_entfallene_termine_werden_nicht_mehr_angeboten(self):
        termin = self.mit_storno_historie()
        termine_entfernen(Termin.objects.filter(pk=termin.pk))
        self.assertNotIn(termin, Termin.objects.buchbar())

    def test_entfallener_termin_ist_nicht_mehr_buchbar(self):
        termin = self.mit_storno_historie()
        termine_entfernen(Termin.objects.filter(pk=termin.pk))
        with self.assertRaises(buchungs_service.TerminNichtVerfuegbar):
            buchungs_service.reservieren(termin.pk, name="Jonas", email="j@example.org")


class DieVierLoeschpfade(Basis):
    """Jeder dieser Wege endete vorher in einem ProtectedError.

    Der fünfte Weg – die Sammellöschung im Django-Admin – war nie darunter:
    Django prüft dort vorher selbst auf geschützte Verweise und zeigt eine
    Hinweisseite, statt zu löschen. Der Fall steht unten trotzdem, damit
    auffällt, falls sich das ändert.
    """

    def test_generator_raeumt_auf_ohne_abzustuerzen(self):
        termin = self.mit_storno_historie()
        # Keine Regel deckt diesen Termin ab, er stammt aber angeblich aus einer.
        Termin.objects.filter(pk=termin.pk).update(herkunft=Termin.Herkunft.REGEL)

        bericht = generiere_termine(self.anna)

        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.ENTFALLEN)
        self.assertEqual(bericht.entfernt, 1)

    def test_sperrzeit_eintragen(self):
        termin = self.mit_storno_historie()
        self.client.force_login(self.chef)
        tag = termin.beginn_lokal.date()

        antwort = self.client.post(
            reverse("termine:sperrzeit_anlegen"),
            {
                "fahrlehrer": self.anna.pk,
                "von_tag": tag.isoformat(),
                "bis_tag": tag.isoformat(),
                "grund": "Urlaub",
            },
        )
        self.assertEqual(antwort.status_code, 302)
        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.ENTFALLEN)
        self.assertTrue(Sperrzeit.objects.exists())

    def test_einzelnen_termin_entfernen(self):
        termin = self.mit_storno_historie()
        self.client.force_login(self.chef)
        antwort = self.client.post(reverse("termine:termin_loeschen", args=[termin.pk]))
        self.assertEqual(antwort.status_code, 302)
        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.ENTFALLEN)

    def test_regel_loeschen(self):
        regel = RhythmusRegel.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            wochentage=[0, 1, 2, 3, 4, 5, 6],
            beginn=dt.time(9, 0),
            ende=dt.time(12, 0),
            gueltig_ab=timezone.localdate(),
        )
        termin = self.mit_storno_historie()
        Termin.objects.filter(pk=termin.pk).update(regel=regel)

        self.client.force_login(self.chef)
        antwort = self.client.post(reverse("termine:regel_loeschen", args=[regel.pk]))
        self.assertEqual(antwort.status_code, 302)
        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.ENTFALLEN)

    def test_admin_lehnt_ab_statt_den_beleg_zu_zerstoeren(self):
        termin = self.mit_storno_historie()
        self.client.force_login(self.chef)
        antwort = self.client.post(
            reverse("admin:termine_termin_changelist"),
            {"action": "delete_selected", "_selected_action": [termin.pk]},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("geschützt", antwort.content.decode())
        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.FREI)
        self.assertEqual(Buchung.objects.filter(termin=termin).count(), 1)


class Wiederbelebung(Basis):
    def test_deckt_die_regel_den_zeitpunkt_wieder_ab_wird_erneut_angeboten(self):
        termin = self.mit_storno_historie(tage_voraus=5, stunde=10)
        termine_entfernen(Termin.objects.filter(pk=termin.pk))
        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.ENTFALLEN)

        # Eine Regel, die genau diese Uhrzeit trifft.
        beginn_lokal = termin.beginn_lokal
        RhythmusRegel.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            wochentage=[beginn_lokal.weekday()],
            beginn=beginn_lokal.time(),
            ende=(beginn_lokal + dt.timedelta(minutes=30)).time(),
            gueltig_ab=timezone.localdate(),
        )
        bericht = generiere_termine(self.anna)

        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.FREI)
        self.assertEqual(bericht.wiederbelebt, 1)
        # Kein zweiter Termin zur selben Uhrzeit.
        self.assertEqual(
            Termin.objects.filter(fahrlehrer=self.anna, beginn=termin.beginn).count(), 1
        )


class Buchungshorizont(Basis):
    """Der Generator und die Anzeige „buchbar bis" müssen denselben Tag meinen."""

    def test_letzter_angebotener_tag_stimmt_mit_dem_generator_ueberein(self):
        from termine.services.verfuegbarkeit import buchungshorizont

        self.anna.horizont_wochen = 4
        self.anna.save(update_fields=["horizont_wochen"])
        RhythmusRegel.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            wochentage=[0, 1, 2, 3, 4, 5, 6],
            beginn=dt.time(9, 0),
            ende=dt.time(10, 0),
            gueltig_ab=timezone.localdate(),
        )
        generiere_termine(self.anna)

        letzter_termin = Termin.objects.order_by("-beginn").first()
        self.assertEqual(letzter_termin.beginn_lokal.date(), buchungshorizont())


class HandplanungNurZukunft(Basis):
    """Dieselbe Zusage wie beim Generator: keine Termine in der Vergangenheit."""

    def test_fenster_das_heute_frueher_begann_faengt_jetzt_an(self):
        from termine.services.planung import termine_manuell_anlegen

        heute = timezone.localdate()
        jetzt = timezone.localtime()
        if jetzt.hour < 2 or jetzt.hour > 21:
            self.skipTest("Zu dicht am Tagesrand für diesen Fall.")

        neue, uebersprungen = termine_manuell_anlegen(
            self.anna, self.art, heute, dt.time(0, 0), dt.time(23, 30)
        )

        self.assertTrue(neue, "Für den Rest des Tages muss etwas entstehen.")
        self.assertTrue(uebersprungen, "Der Vormittag muss übersprungen worden sein.")
        for termin in neue:
            self.assertGreaterEqual(termin.beginn, jetzt)

    def test_raster_bleibt_am_fensterbeginn_haengen(self):
        # „ab 9 Uhr, 30 Minuten" darf nicht zu 14:07 führen, nur weil es
        # gerade 14:07 ist.
        from termine.services.planung import termine_manuell_anlegen

        heute = timezone.localdate()
        neue, _ = termine_manuell_anlegen(
            self.anna, self.art, heute, dt.time(0, 0), dt.time(23, 30)
        )
        for termin in neue:
            self.assertIn(termin.beginn_lokal.minute, (0, 30))

    def test_morgen_bleibt_vollstaendig(self):
        from termine.services.planung import termine_manuell_anlegen

        morgen = timezone.localdate() + dt.timedelta(days=1)
        neue, uebersprungen = termine_manuell_anlegen(
            self.anna, self.art, morgen, dt.time(9, 0), dt.time(11, 0)
        )
        self.assertEqual(len(neue), 4)
        self.assertEqual(uebersprungen, 0)


class Mindestvorlauf(Basis):
    """Beide Wege durch `buchbar()` – gleicher Vorlauf und unterschiedlicher."""

    def termin_in(self, stunden: int, fahrlehrer=None) -> Termin:
        beginn = timezone.now() + dt.timedelta(hours=stunden)
        return Termin.objects.create(
            fahrlehrer=fahrlehrer or self.anna,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )

    def test_gleicher_vorlauf_bei_allen(self):
        self.anna.vorlauf_stunden = 24
        self.anna.save(update_fields=["vorlauf_stunden"])

        zu_frueh = self.termin_in(2)
        passt = self.termin_in(48)

        buchbar = list(Termin.objects.buchbar())
        self.assertIn(passt, buchbar)
        self.assertNotIn(zu_frueh, buchbar)

    def test_unterschiedlicher_vorlauf_wird_je_fahrlehrer_gerechnet(self):
        self.anna.vorlauf_stunden = 48
        self.anna.save(update_fields=["vorlauf_stunden"])
        tom = Fahrlehrer.objects.create(
            name="Tom Keller", email="tom@example.org", bundesland="BY", vorlauf_stunden=2
        )

        annas_termin = self.termin_in(24)  # zu kurzfristig für Anna
        toms_termin = self.termin_in(24, fahrlehrer=tom)  # für Tom weit genug weg

        buchbar = list(Termin.objects.buchbar())
        self.assertIn(toms_termin, buchbar)
        self.assertNotIn(annas_termin, buchbar)

    def test_ohne_aktive_fahrlehrer_ist_nichts_buchbar(self):
        self.termin_in(48)
        Fahrlehrer.objects.update(aktiv=False)
        self.assertEqual(Termin.objects.buchbar().count(), 0)
