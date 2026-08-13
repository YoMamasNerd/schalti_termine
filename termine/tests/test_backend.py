"""Tests für den internen Bereich: Zugriffsschutz, Tagesplanung, Absagen."""

import datetime as dt

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models import Buchung, Fahrlehrer, RhythmusRegel, Sperrzeit, Termin, Terminart
from termine.services import buchung as buchungs_service


class BackendBasis(TestCase):
    def setUp(self):
        User = get_user_model()
        self.art = Terminart.objects.create(name="Erstberatung", dauer_minuten=30)

        self.anna = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )
        self.tom = Fahrlehrer.objects.create(
            name="Tom Keller", email="tom@example.org", bundesland="BE", vorlauf_stunden=0
        )

        self.chef = User.objects.create_user("chef", password="geheim123", is_staff=True)
        self.anna_login = User.objects.create_user("anna", password="geheim123")
        self.anna.benutzer = self.anna_login
        self.anna.save()

        self.montag = timezone.localdate() + dt.timedelta(days=7)
        while self.montag.weekday() != 0:
            self.montag += dt.timedelta(days=1)

    def termin_fuer(self, fahrlehrer, stunde=10, tage_voraus=7) -> Termin:
        beginn = (timezone.now() + dt.timedelta(days=tage_voraus)).replace(
            hour=stunde, minute=0, second=0, microsecond=0
        )
        return Termin.objects.create(
            fahrlehrer=fahrlehrer,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )


class Zugriffsschutz(BackendBasis):
    def test_ohne_login_zur_anmeldung(self):
        for name in ("termine:dashboard", "termine:tagesplanung", "termine:buchungen"):
            with self.subTest(seite=name):
                antwort = self.client.get(reverse(name))
                self.assertEqual(antwort.status_code, 302)
                self.assertIn("/intern/anmelden/", antwort["Location"])

    def test_benutzer_ohne_fahrlehrerprofil_darf_nicht_rein(self):
        get_user_model().objects.create_user("fremd", password="geheim123")
        self.client.login(username="fremd", password="geheim123")

        antwort = self.client.get(reverse("termine:dashboard"))

        self.assertEqual(antwort.status_code, 403)

    def test_fahrlehrer_sieht_nur_die_eigenen_buchungen(self):
        eigener = self.termin_fuer(self.anna)
        fremder = self.termin_fuer(self.tom, stunde=11)
        Buchung.objects.create(
            termin=eigener, name="Kunde Anna", email="a@example.org",
            status=Buchung.Status.BESTAETIGT,
        )
        Buchung.objects.create(
            termin=fremder, name="Kunde Tom", email="t@example.org",
            status=Buchung.Status.BESTAETIGT,
        )

        self.client.login(username="anna", password="geheim123")
        antwort = self.client.get(reverse("termine:buchungen"))

        self.assertContains(antwort, "Kunde Anna")
        self.assertNotContains(antwort, "Kunde Tom")

    def test_staff_sieht_alle_buchungen(self):
        Buchung.objects.create(
            termin=self.termin_fuer(self.tom), name="Kunde Tom", email="t@example.org",
            status=Buchung.Status.BESTAETIGT,
        )

        self.client.login(username="chef", password="geheim123")
        antwort = self.client.get(reverse("termine:buchungen"))

        self.assertContains(antwort, "Kunde Tom")

    def test_fahrlehrer_kann_fremde_termine_nicht_loeschen(self):
        fremder = self.termin_fuer(self.tom)

        self.client.login(username="anna", password="geheim123")
        antwort = self.client.post(reverse("termine:termin_loeschen", args=[fremder.pk]))

        self.assertEqual(antwort.status_code, 404)
        self.assertTrue(Termin.objects.filter(pk=fremder.pk).exists())


class Tagesplanung(BackendBasis):
    def setUp(self):
        super().setUp()
        self.client.login(username="chef", password="geheim123")

    def test_wochenansicht_zeigt_die_termine(self):
        self.termin_fuer(self.anna, stunde=14)

        antwort = self.client.get(
            reverse("termine:tagesplanung"), {"fahrlehrer": self.anna.slug}
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Anna Berger")

    def test_einzelnen_tag_beplanen(self):
        tag = self.montag + dt.timedelta(days=2)

        antwort = self.client.post(
            reverse("termine:termine_anlegen"),
            {
                "fahrlehrer": self.anna.pk,
                "terminart": self.art.pk,
                "tag": tag.isoformat(),
                "von": "14:00",
                "bis": "16:00",
                "notiz": "Nachmittagssprechstunde",
            },
        )

        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(Termin.objects.filter(fahrlehrer=self.anna).count(), 4)
        self.assertTrue(
            Termin.objects.filter(herkunft=Termin.Herkunft.MANUELL).exists()
        )

    def test_vergangener_tag_wird_abgelehnt(self):
        antwort = self.client.post(
            reverse("termine:termine_anlegen"),
            {
                "fahrlehrer": self.anna.pk,
                "terminart": self.art.pk,
                "tag": (timezone.localdate() - dt.timedelta(days=1)).isoformat(),
                "von": "14:00",
                "bis": "16:00",
            },
            follow=True,
        )

        self.assertEqual(Termin.objects.count(), 0)
        self.assertContains(antwort, "Vergangenheit")

    def test_freien_termin_loeschen(self):
        termin = self.termin_fuer(self.anna)

        self.client.post(reverse("termine:termin_loeschen", args=[termin.pk]))

        self.assertFalse(Termin.objects.filter(pk=termin.pk).exists())

    def test_gebuchter_termin_wird_nicht_geloescht(self):
        termin = self.termin_fuer(self.anna)
        termin.status = Termin.Status.GEBUCHT
        termin.save()

        antwort = self.client.post(
            reverse("termine:termin_loeschen", args=[termin.pk]), follow=True
        )

        self.assertTrue(Termin.objects.filter(pk=termin.pk).exists())
        self.assertContains(antwort, "belegt")

    def test_sperrzeit_raeumt_freie_termine_ab(self):
        tag = self.montag + dt.timedelta(days=2)
        self.client.post(
            reverse("termine:termine_anlegen"),
            {
                "fahrlehrer": self.anna.pk,
                "terminart": self.art.pk,
                "tag": tag.isoformat(),
                "von": "14:00",
                "bis": "16:00",
            },
        )
        self.assertEqual(Termin.objects.count(), 4)

        self.client.post(
            reverse("termine:sperrzeit_anlegen"),
            {
                "fahrlehrer": self.anna.pk,
                "von_tag": tag.isoformat(),
                "bis_tag": tag.isoformat(),
                "grund": "Urlaub",
            },
        )

        self.assertEqual(Termin.objects.count(), 0)
        self.assertEqual(Sperrzeit.objects.count(), 1)

    def test_generator_laesst_sich_von_hand_anstossen(self):
        RhythmusRegel.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            wochentage=[0, 1, 2, 3, 4],
            beginn=dt.time(9, 0),
            ende=dt.time(11, 0),
            gueltig_ab=timezone.localdate(),
        )

        antwort = self.client.post(
            reverse("termine:generieren"), {"fahrlehrer": self.anna.slug}, follow=True
        )

        self.assertContains(antwort, "erstellt")
        self.assertGreater(Termin.objects.filter(fahrlehrer=self.anna).count(), 0)


class BuchungAbsagen(BackendBasis):
    def test_fahrschule_sagt_ab_und_gibt_den_termin_frei(self):
        termin = self.termin_fuer(self.anna)
        with self.captureOnCommitCallbacks(execute=True):
            buchung = buchungs_service.bestaetigen(
                buchungs_service.reservieren(
                    termin.pk, name="Max Muster", email="max@example.org"
                )
            )

        self.client.login(username="chef", password="geheim123")
        with self.captureOnCommitCallbacks(execute=True):
            antwort = self.client.post(
                reverse("termine:buchung_absagen", args=[buchung.pk]), follow=True
            )

        buchung.refresh_from_db()
        termin.refresh_from_db()
        self.assertEqual(buchung.status, Buchung.Status.STORNIERT)
        self.assertEqual(buchung.storniert_von, "fahrschule")
        self.assertEqual(termin.status, Termin.Status.FREI)
        self.assertContains(antwort, "abgesagt")


class RegelVerwaltung(BackendBasis):
    def setUp(self):
        super().setUp()
        self.client.login(username="chef", password="geheim123")

    def test_regel_anlegen_und_direkt_generieren(self):
        antwort = self.client.post(
            reverse("termine:regel_neu"),
            {
                "fahrlehrer": self.anna.pk,
                "terminart": self.art.pk,
                "bezeichnung": "Dienstags nachmittags",
                "wochentage": ["1", "3"],
                "beginn": "14:00",
                "ende": "17:00",
                "intervall_wochen": "1",
                "referenzwoche": timezone.localdate().isoformat(),
                "gueltig_ab": timezone.localdate().isoformat(),
                "feiertage_auslassen": "on",
                "aktiv": "on",
                "speichern_und_generieren": "1",
            },
            follow=True,
        )

        self.assertEqual(antwort.status_code, 200)
        regel = RhythmusRegel.objects.get()
        self.assertEqual(regel.wochentage, [1, 3])
        self.assertGreater(Termin.objects.count(), 0)

    def test_regel_ohne_wochentag_wird_abgelehnt(self):
        antwort = self.client.post(
            reverse("termine:regel_neu"),
            {
                "fahrlehrer": self.anna.pk,
                "terminart": self.art.pk,
                "wochentage": [],
                "beginn": "14:00",
                "ende": "17:00",
                "intervall_wochen": "1",
                "referenzwoche": timezone.localdate().isoformat(),
                "gueltig_ab": timezone.localdate().isoformat(),
            },
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(RhythmusRegel.objects.count(), 0)

    def test_regel_loeschen_behaelt_gebuchte_termine(self):
        regel = RhythmusRegel.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            wochentage=[0],
            beginn=dt.time(9, 0),
            ende=dt.time(11, 0),
            gueltig_ab=timezone.localdate(),
        )
        frei = self.termin_fuer(self.anna, stunde=9)
        frei.regel = regel
        frei.save()
        gebucht = self.termin_fuer(self.anna, stunde=15)
        gebucht.regel = regel
        gebucht.status = Termin.Status.GEBUCHT
        gebucht.save()

        self.client.post(reverse("termine:regel_loeschen", args=[regel.pk]))

        self.assertFalse(Termin.objects.filter(pk=frei.pk).exists())
        self.assertTrue(Termin.objects.filter(pk=gebucht.pk).exists())
        self.assertEqual(RhythmusRegel.objects.count(), 0)
