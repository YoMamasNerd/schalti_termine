"""Tests für den Buchungsablauf, das Double-Opt-in und die öffentliche Seite."""

import datetime as dt

from django.core import mail as django_mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models import Buchung, Fahrlehrer, Termin, Terminart
from termine.services import buchung as buchungs_service
from termine.services.ics import buchung_als_ics, fahrlehrer_feed


class BuchungsBasis(TestCase):
    def setUp(self):
        self.art = Terminart.objects.create(
            name="Erstberatung", dauer_minuten=30, ort="Hauptstraße 1"
        )
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Anna Berger",
            email="anna@example.org",
            bundesland="BW",
            vorlauf_stunden=1,
        )
        self.termin = self.neuer_termin()

    def neuer_termin(self, tage_voraus: int = 5, stunde: int = 10) -> Termin:
        beginn = (timezone.now() + dt.timedelta(days=tage_voraus)).replace(
            hour=stunde, minute=0, second=0, microsecond=0
        )
        return Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
            status=Termin.Status.FREI,
        )

    def mit_commit(self, funktion, *args, **kwargs):
        """Führt eine Service-Funktion aus und löst dabei die on_commit-Hooks aus.

        `TestCase` rollt jede Transaktion zurück, deshalb würden die per
        `transaction.on_commit` registrierten Mailversände sonst nie laufen.
        """
        with self.captureOnCommitCallbacks(execute=True):
            return funktion(*args, **kwargs)

    def bestaetigen(self, buchung):
        return self.mit_commit(buchungs_service.bestaetigen, buchung)

    def stornieren(self, buchung, **kwargs):
        return self.mit_commit(buchungs_service.stornieren, buchung, **kwargs)

    def reservieren(self, termin=None, **abweichend):
        daten = {
            "name": "Max Muster",
            "email": "max@example.org",
            "telefon": "0170 1234567",
            "fuehrerscheinklasse": "B",
            "nachricht": "Ich bin 17 und möchte BF17 machen.",
        }
        daten.update(abweichend)
        return self.mit_commit(
            buchungs_service.reservieren, (termin or self.termin).pk, **daten
        )


class DoppelOptIn(BuchungsBasis):
    def test_reservierung_blockiert_den_termin_und_schickt_eine_mail(self):
        buchung = self.reservieren()
        self.termin.refresh_from_db()

        self.assertEqual(buchung.status, Buchung.Status.OFFEN)
        self.assertEqual(self.termin.status, Termin.Status.RESERVIERT)
        self.assertIsNotNone(buchung.reserviert_bis)
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn(buchung.token, django_mail.outbox[0].body)

    def test_bestaetigung_macht_die_buchung_verbindlich(self):
        buchung = self.reservieren()
        django_mail.outbox.clear()

        buchung = self.bestaetigen(buchung)
        self.termin.refresh_from_db()

        self.assertEqual(buchung.status, Buchung.Status.BESTAETIGT)
        self.assertEqual(self.termin.status, Termin.Status.GEBUCHT)
        self.assertIsNone(buchung.reserviert_bis)
        # Je eine Mail an den Kunden und an den Fahrlehrer.
        self.assertEqual(len(django_mail.outbox), 2)
        empfaenger = {adresse for nachricht in django_mail.outbox for adresse in nachricht.to}
        self.assertEqual(empfaenger, {"max@example.org", "anna@example.org"})

    def test_bestaetigungsmail_enthaelt_kalendereintrag(self):
        buchung = self.reservieren()
        django_mail.outbox.clear()  # die Anfrage-Mail selbst hat noch keinen Anhang

        self.bestaetigen(buchung)

        kunden_mail = next(n for n in django_mail.outbox if "max@example.org" in n.to)

        anhaenge = {name: inhalt for name, inhalt, _ in kunden_mail.attachments}
        self.assertIn("termin.ics", anhaenge)
        # Django gibt Text-Anhänge bereits dekodiert zurück.
        inhalt = anhaenge["termin.ics"]
        if isinstance(inhalt, bytes):
            inhalt = inhalt.decode()
        self.assertIn("BEGIN:VEVENT", inhalt)
        self.assertIn("Erstberatung", inhalt)

    def test_doppeltes_bestaetigen_ist_unschaedlich(self):
        buchung = self.bestaetigen(self.reservieren())
        anzahl_mails = len(django_mail.outbox)

        nochmal = self.bestaetigen(buchung)

        self.assertEqual(nochmal.status, Buchung.Status.BESTAETIGT)
        self.assertEqual(len(django_mail.outbox), anzahl_mails)  # keine zweite Mail

    def test_abgelaufene_reservierung_kann_nicht_bestaetigt_werden(self):
        buchung = self.reservieren()
        buchung.reserviert_bis = timezone.now() - dt.timedelta(minutes=1)
        buchung.save()

        with self.assertRaises(buchungs_service.BuchungsFehler):
            buchungs_service.bestaetigen(buchung)

    def test_abgelaufene_reservierung_gibt_den_termin_frei(self):
        buchung = self.reservieren()
        buchung.reserviert_bis = timezone.now() - dt.timedelta(minutes=1)
        buchung.save()

        anzahl = buchungs_service.abgelaufene_reservierungen_freigeben()

        buchung.refresh_from_db()
        self.termin.refresh_from_db()
        self.assertEqual(anzahl, 1)
        self.assertEqual(buchung.status, Buchung.Status.VERFALLEN)
        self.assertEqual(self.termin.status, Termin.Status.FREI)

    def test_bestaetigte_buchung_laeuft_nicht_ab(self):
        self.bestaetigen(self.reservieren())

        self.assertEqual(buchungs_service.abgelaufene_reservierungen_freigeben(), 0)
        self.termin.refresh_from_db()
        self.assertEqual(self.termin.status, Termin.Status.GEBUCHT)


class DoppelbuchungsSchutz(BuchungsBasis):
    def test_zweiter_interessent_bekommt_den_termin_nicht(self):
        self.reservieren()

        with self.assertRaises(buchungs_service.TerminNichtVerfuegbar):
            self.reservieren(name="Erika Muster", email="erika@example.org")

        self.assertEqual(Buchung.objects.count(), 1)

    def test_termin_unterhalb_der_vorlaufzeit_ist_gesperrt(self):
        gleich = self.neuer_termin(tage_voraus=0)
        gleich.beginn = timezone.now() + dt.timedelta(minutes=10)
        gleich.ende = gleich.beginn + dt.timedelta(minutes=30)
        gleich.save()

        with self.assertRaises(buchungs_service.TerminNichtVerfuegbar):
            self.reservieren(termin=gleich)

    def test_nach_storno_ist_der_termin_wieder_buchbar(self):
        buchung = self.bestaetigen(self.reservieren())

        self.stornieren(buchung, von="kunde")

        self.termin.refresh_from_db()
        self.assertEqual(self.termin.status, Termin.Status.FREI)

        # Und jemand anderes kann ihn jetzt tatsächlich bekommen.
        neue = self.reservieren(name="Erika Muster", email="erika@example.org")
        self.assertEqual(neue.status, Buchung.Status.OFFEN)
        self.assertEqual(Buchung.objects.count(), 2)


class Storno(BuchungsBasis):
    def test_kundenstorno_benachrichtigt_beide_seiten(self):
        buchung = self.bestaetigen(self.reservieren())
        django_mail.outbox.clear()

        self.stornieren(buchung, von="kunde")

        buchung.refresh_from_db()
        self.assertEqual(buchung.status, Buchung.Status.STORNIERT)
        self.assertEqual(len(django_mail.outbox), 2)

    def test_absage_durch_die_fahrschule_benachrichtigt_den_kunden(self):
        buchung = self.bestaetigen(self.reservieren())
        django_mail.outbox.clear()

        self.stornieren(buchung, von="fahrschule")

        buchung.refresh_from_db()
        self.assertEqual(buchung.storniert_von, "fahrschule")
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn("max@example.org", django_mail.outbox[0].to)

    def test_storno_ics_ist_als_absage_gekennzeichnet(self):
        buchung = self.bestaetigen(self.reservieren())
        inhalt = buchung_als_ics(buchung, storniert=True).decode()

        self.assertIn("METHOD:CANCEL", inhalt)
        self.assertIn("STATUS:CANCELLED", inhalt)

    def test_doppeltes_stornieren_ist_unschaedlich(self):
        buchung = self.bestaetigen(self.reservieren())
        self.stornieren(buchung, von="kunde")
        django_mail.outbox.clear()

        self.stornieren(buchung, von="kunde")

        self.assertEqual(len(django_mail.outbox), 0)


class Erinnerungen(BuchungsBasis):
    def test_erinnerung_geht_genau_einmal_raus(self):
        morgen = self.neuer_termin(tage_voraus=0)
        morgen.beginn = timezone.now() + dt.timedelta(hours=5)
        morgen.ende = morgen.beginn + dt.timedelta(minutes=30)
        morgen.save()
        self.bestaetigen(self.reservieren(termin=morgen))
        django_mail.outbox.clear()

        self.assertEqual(buchungs_service.erinnerungen_versenden(stunden_vorher=24), 1)
        self.assertEqual(len(django_mail.outbox), 1)
        # Ein zweiter Lauf darf nicht noch einmal erinnern.
        self.assertEqual(buchungs_service.erinnerungen_versenden(stunden_vorher=24), 0)

    def test_weit_entfernte_termine_werden_nicht_erinnert(self):
        self.bestaetigen(self.reservieren())  # in 5 Tagen
        django_mail.outbox.clear()

        self.assertEqual(buchungs_service.erinnerungen_versenden(stunden_vorher=24), 0)
        self.assertEqual(len(django_mail.outbox), 0)


class Datenschutz(BuchungsBasis):
    def test_alte_buchungen_werden_anonymisiert(self):
        buchung = self.bestaetigen(self.reservieren())
        # Termin künstlich weit in die Vergangenheit schieben.
        Termin.objects.filter(pk=self.termin.pk).update(
            beginn=timezone.now() - dt.timedelta(days=400),
            ende=timezone.now() - dt.timedelta(days=400) + dt.timedelta(minutes=30),
        )

        anzahl = buchungs_service.alte_buchungen_anonymisieren(tage=180)

        buchung.refresh_from_db()
        self.assertEqual(anzahl, 1)
        self.assertEqual(buchung.email, "")
        self.assertEqual(buchung.telefon, "")
        self.assertEqual(buchung.nachricht, "")
        self.assertNotEqual(buchung.name, "Max Muster")
        self.assertIsNotNone(buchung.anonymisiert_am)

    def test_aktuelle_buchungen_bleiben_unangetastet(self):
        buchung = self.bestaetigen(self.reservieren())

        self.assertEqual(buchungs_service.alte_buchungen_anonymisieren(tage=180), 0)

        buchung.refresh_from_db()
        self.assertEqual(buchung.email, "max@example.org")


class IcsFeed(BuchungsBasis):
    def test_feed_enthaelt_nur_bestaetigte_termine(self):
        self.bestaetigen(self.reservieren())
        zweiter = self.neuer_termin(tage_voraus=6)
        self.reservieren(termin=zweiter, email="offen@example.org")  # bleibt offen

        inhalt = fahrlehrer_feed(self.fahrlehrer).decode()

        self.assertEqual(inhalt.count("BEGIN:VEVENT"), 1)
        self.assertIn("Max Muster", inhalt)
        self.assertNotIn("offen@example.org", inhalt)

    def test_feed_ist_nur_mit_token_erreichbar(self):
        self.bestaetigen(self.reservieren())

        antwort = self.client.get(
            reverse("termine:ics_feed", args=[self.fahrlehrer.feed_token])
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("text/calendar", antwort["Content-Type"])

        falsch = self.client.get(reverse("termine:ics_feed", args=["falscher-token"]))
        self.assertEqual(falsch.status_code, 404)


class OeffentlicheSeiten(BuchungsBasis):
    def test_startseite_zeigt_freie_termine(self):
        antwort = self.client.get(reverse("termine:start"))

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Beratungstermin buchen")
        self.assertContains(antwort, self.termin.beginn_lokal.strftime("%H:%M"))

    def test_buchungsformular_wird_angezeigt(self):
        antwort = self.client.get(reverse("termine:buchen", args=[self.termin.pk]))

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Erstberatung")

    def test_absenden_legt_eine_offene_buchung_an(self):
        antwort = self.client.post(
            reverse("termine:buchen", args=[self.termin.pk]),
            {
                "name": "Max Muster",
                "email": "max@example.org",
                "telefon": "",
                "fuehrerscheinklasse": "B",
                "nachricht": "",
                "datenschutz": "on",
                "website": "",
            },
        )

        buchung = Buchung.objects.get()
        self.assertRedirects(antwort, reverse("termine:buchung", args=[buchung.token]))
        self.assertEqual(buchung.status, Buchung.Status.OFFEN)
        self.assertIsNotNone(buchung.einwilligung_am)

    def test_ohne_datenschutzhaken_keine_buchung(self):
        antwort = self.client.post(
            reverse("termine:buchen", args=[self.termin.pk]),
            {"name": "Max Muster", "email": "max@example.org", "website": ""},
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(Buchung.objects.count(), 0)

    def test_honigtopf_faengt_bots_ab(self):
        antwort = self.client.post(
            reverse("termine:buchen", args=[self.termin.pk]),
            {
                "name": "Bot",
                "email": "bot@example.org",
                "datenschutz": "on",
                "website": "http://spam.example",
            },
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(Buchung.objects.count(), 0)

    def test_bestaetigungslink_bucht_verbindlich(self):
        buchung = self.reservieren()

        antwort = self.client.get(reverse("termine:bestaetigen", args=[buchung.token]))

        buchung.refresh_from_db()
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(buchung.status, Buchung.Status.BESTAETIGT)
        self.assertContains(antwort, "gebucht")

    def test_unbekannter_token_fuehrt_ins_leere(self):
        antwort = self.client.get(reverse("termine:bestaetigen", args=["quatsch"]))
        self.assertEqual(antwort.status_code, 404)

    def test_kunde_kann_ueber_den_link_stornieren(self):
        buchung = self.bestaetigen(self.reservieren())

        antwort = self.client.post(
            reverse("termine:buchung_stornieren", args=[buchung.token])
        )

        buchung.refresh_from_db()
        self.assertRedirects(antwort, reverse("termine:buchung", args=[buchung.token]))
        self.assertEqual(buchung.status, Buchung.Status.STORNIERT)

    def test_vergebener_termin_ist_nicht_mehr_buchbar(self):
        self.reservieren()

        antwort = self.client.get(reverse("termine:buchen", args=[self.termin.pk]))

        self.assertEqual(antwort.status_code, 409)
        self.assertContains(antwort, "leider weg", status_code=409)

    def test_ohne_auswahlmoeglichkeit_keine_filterleiste(self):
        """Ein Fahrlehrer, eine Terminart – der Kunde soll nichts auswählen müssen."""
        antwort = self.client.get(reverse("termine:start"))

        self.assertNotContains(antwort, 'name="fahrlehrer"')
        self.assertNotContains(antwort, 'name="art"')
        self.assertNotContains(antwort, "Termine anzeigen")
        # Der Kalender ist trotzdem da.
        self.assertContains(antwort, "buchungsbereich")

    def test_einziger_fahrlehrer_wird_still_verwendet(self):
        """Ohne Auswahl gilt implizit der eine Fahrlehrer – ohne Parameter in der URL."""
        self.fahrlehrer.beschreibung = "Klassen B und BE."
        self.fahrlehrer.save()

        antwort = self.client.get(reverse("termine:start"))

        # Seine Beschreibung erscheint, sein Name steht nicht an jedem Termin.
        self.assertContains(antwort, "Klassen B und BE.")
        self.assertNotContains(antwort, '<br>Anna Berger')
        # Die Navigationslinks bleiben frei von einem überflüssigen Parameter.
        self.assertNotContains(antwort, "fahrlehrer=anna-berger")

    def test_bei_mehreren_fahrlehrern_gibt_es_die_auswahl(self):
        Fahrlehrer.objects.create(
            name="Tom Keller", email="tom@example.org", bundesland="BE", vorlauf_stunden=1
        )

        antwort = self.client.get(reverse("termine:start"))

        self.assertContains(antwort, 'name="fahrlehrer"')
        self.assertContains(antwort, "Egal – alle anzeigen")
        self.assertContains(antwort, "Tom Keller")

    def test_bei_mehreren_terminarten_gibt_es_die_auswahl(self):
        Terminart.objects.create(name="Ausführliche Beratung", dauer_minuten=60)

        antwort = self.client.get(reverse("termine:start"))

        self.assertContains(antwort, 'name="art"')
        self.assertContains(antwort, "Alle Terminarten")

    def test_gebuchte_termine_verschwinden_von_der_startseite(self):
        self.bestaetigen(self.reservieren())

        antwort = self.client.get(reverse("termine:start"))

        self.assertNotContains(antwort, f'href="/buchen/{self.termin.pk}/"')
