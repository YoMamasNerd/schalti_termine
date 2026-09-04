"""Tests für den Django-Admin.

Die Aktionen dort sind einen Klick weit von folgenreich entfernt: Ein
zurückgesetzter Feed-Token macht ein Kalender-Abo ungültig, eine Absage
schickt dem Kunden eine E-Mail, und das Löschen von Terminen darf gebuchte
Zeiten nicht mitreißen.
"""

import datetime as dt

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail as django_mail
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from termine.admin import TerminAdmin
from termine.models import Buchung, Fahrlehrer, RhythmusRegel, Termin, Terminart


class AdminBasis(TestCase):
    def setUp(self):
        self.chef = get_user_model().objects.create_superuser(
            "chef", "chef@example.org", "geheim123"
        )
        self.client.force_login(self.chef)

        self.art = Terminart.objects.create(name="Beratung", dauer_minuten=30)
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )

    def termin(self, stunden_voraus=48, status=Termin.Status.FREI):
        beginn = (timezone.now() + dt.timedelta(hours=stunden_voraus)).replace(
            minute=0, second=0, microsecond=0
        )
        return Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
            status=status,
        )

    def aktion(self, modell, name, objekte):
        """Führt eine Admin-Aktion so aus, wie es die Oberfläche täte."""
        url = reverse(f"admin:termine_{modell}_changelist")
        return self.client.post(
            url,
            {
                "action": name,
                "_selected_action": [str(o.pk) for o in objekte],
                "index": "0",
            },
            follow=True,
        )

    def anfrage_mit_meldungen(self):
        anfrage = RequestFactory().post("/")
        anfrage.user = self.chef
        anfrage.session = self.client.session
        setattr(anfrage, "_messages", FallbackStorage(anfrage))
        return anfrage


class Erreichbarkeit(AdminBasis):
    def test_alle_uebersichtsseiten_laden(self):
        for modell in ("fahrlehrer", "terminart", "rhythmusregel", "sperrzeit", "termin", "buchung"):
            with self.subTest(modell=modell):
                antwort = self.client.get(reverse(f"admin:termine_{modell}_changelist"))
                self.assertEqual(antwort.status_code, 200)

    def test_fahrlehrer_formular_zeigt_die_abo_url(self):
        antwort = self.client.get(
            reverse("admin:termine_fahrlehrer_change", args=[self.fahrlehrer.pk])
        )
        self.assertContains(antwort, self.fahrlehrer.feed_token)

    def test_buchungsformular_zeigt_die_links(self):
        buchung = Buchung.objects.create(
            termin=self.termin(), name="Max Muster", email="max@example.org",
            status=Buchung.Status.BESTAETIGT,
        )
        antwort = self.client.get(reverse("admin:termine_buchung_change", args=[buchung.pk]))
        self.assertContains(antwort, buchung.token)


class TermineErzeugenAktion(AdminBasis):
    def regel(self):
        return RhythmusRegel.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            wochentage=[0, 1, 2, 3, 4, 5, 6],
            beginn=dt.time(9, 0),
            ende=dt.time(11, 0),
            gueltig_ab=timezone.localdate(),
        )

    def test_ueber_den_fahrlehrer(self):
        self.regel()

        antwort = self.aktion("fahrlehrer", "termine_generieren", [self.fahrlehrer])

        self.assertEqual(antwort.status_code, 200)
        self.assertGreater(Termin.objects.count(), 0)

    def test_ueber_die_regel(self):
        regel = self.regel()

        self.aktion("rhythmusregel", "termine_generieren", [regel])

        self.assertGreater(Termin.objects.count(), 0)


class FeedTokenAktion(AdminBasis):
    def test_zuruecksetzen_erzeugt_einen_neuen_token(self):
        alt = self.fahrlehrer.feed_token

        self.aktion("fahrlehrer", "feed_token_zuruecksetzen", [self.fahrlehrer])

        self.fahrlehrer.refresh_from_db()
        self.assertNotEqual(self.fahrlehrer.feed_token, alt)
        self.assertGreaterEqual(len(self.fahrlehrer.feed_token), 40)

    def test_altes_abo_wird_dadurch_ungueltig(self):
        alt = self.fahrlehrer.feed_token
        self.assertEqual(self.client.get(reverse("termine:ics_feed", args=[alt])).status_code, 200)

        self.aktion("fahrlehrer", "feed_token_zuruecksetzen", [self.fahrlehrer])

        self.fahrlehrer.refresh_from_db()
        self.assertEqual(self.client.get(reverse("termine:ics_feed", args=[alt])).status_code, 404)
        self.assertEqual(
            self.client.get(
                reverse("termine:ics_feed", args=[self.fahrlehrer.feed_token])
            ).status_code,
            200,
        )


class BuchungAbsagenAktion(AdminBasis):
    def test_absage_informiert_den_kunden_und_gibt_frei(self):
        termin = self.termin(status=Termin.Status.GEBUCHT)
        buchung = Buchung.objects.create(
            termin=termin, name="Max Muster", email="max@example.org",
            status=Buchung.Status.BESTAETIGT,
        )
        django_mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            self.aktion("buchung", "absagen", [buchung])

        buchung.refresh_from_db()
        termin.refresh_from_db()
        self.assertEqual(buchung.status, Buchung.Status.STORNIERT)
        self.assertEqual(buchung.storniert_von, "fahrschule")
        self.assertEqual(termin.status, Termin.Status.FREI)
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn("max@example.org", django_mail.outbox[0].to)

    def test_bereits_stornierte_buchung_wird_uebersprungen(self):
        buchung = Buchung.objects.create(
            termin=self.termin(), name="Max Muster", email="max@example.org",
            status=Buchung.Status.STORNIERT,
        )
        django_mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            self.aktion("buchung", "absagen", [buchung])

        self.assertEqual(len(django_mail.outbox), 0)


class TermineLoeschen(AdminBasis):
    def test_belegte_termine_werden_verschont(self):
        frei = self.termin(stunden_voraus=48)
        gebucht = self.termin(stunden_voraus=72, status=Termin.Status.GEBUCHT)

        TerminAdmin(Termin, site).delete_queryset(
            self.anfrage_mit_meldungen(), Termin.objects.all()
        )

        self.assertFalse(Termin.objects.filter(pk=frei.pk).exists())
        self.assertTrue(Termin.objects.filter(pk=gebucht.pk).exists())


class Anzeigespalten(AdminBasis):
    def test_kundenspalte_zeigt_den_namen_oder_strich(self):
        admin = TerminAdmin(Termin, site)
        leer = self.termin(stunden_voraus=48)
        belegt = self.termin(stunden_voraus=72, status=Termin.Status.GEBUCHT)
        Buchung.objects.create(
            termin=belegt, name="Max Muster", email="max@example.org",
            status=Buchung.Status.BESTAETIGT,
        )

        self.assertEqual(admin.kunde(leer), "—")
        self.assertEqual(admin.kunde(belegt), "Max Muster")

    def test_rhythmusspalte_beschreibt_den_takt(self):
        from termine.admin import RhythmusRegelAdmin

        admin = RhythmusRegelAdmin(RhythmusRegel, site)
        woechentlich = RhythmusRegel.objects.create(
            fahrlehrer=self.fahrlehrer, terminart=self.art, wochentage=[0],
            beginn=dt.time(9, 0), ende=dt.time(11, 0), gueltig_ab=timezone.localdate(),
        )
        zweiwoechig = RhythmusRegel.objects.create(
            fahrlehrer=self.fahrlehrer, terminart=self.art, wochentage=[2],
            beginn=dt.time(9, 0), ende=dt.time(11, 0), gueltig_ab=timezone.localdate(),
            intervall_wochen=2,
        )

        self.assertIn("wöchentlich", admin.rhythmus(woechentlich))
        self.assertIn("alle 2 Wochen", admin.rhythmus(zweiwoechig))
