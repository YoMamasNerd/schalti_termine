"""End-to-End Test für den gesamten Buchungslebenszyklus nach der schalti_ui-Migration.

Simuliert den vollständigen Kunden- und Fahrschulprozess:
1. Aufruf der Startseite mit Kalenderübersicht
2. Auswahl eines freien Termins und Laden des Buchungsformulars
3. Absenden der Buchungsanfrage (Reservierung mit Double-Opt-In-Start)
4. Aufruf der Double-Opt-In-Bestätigungsseite
5. Verbindliche Bestätigung per POST
6. Aufruf der kundeninternen Terminansicht (Token)
7. Stornierung durch den Kunden (Termin wird wieder freigegeben)
8. DSGVO-Löschung („Recht auf Vergessenwerden“)
"""

import datetime as dt

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models import Buchung, Fahrlehrer, Fuehrerscheinklasse, Termin, Terminart


class BuchungsprozessE2ETest(TestCase):
    def setUp(self):
        self.klasse = Fuehrerscheinklasse.objects.create(
            code="B",
            name="Klasse B (PKW)",
            aktiv=True,
            reihenfolge=1,
        )
        self.art = Terminart.objects.create(
            name="Erstberatung Führerschein",
            dauer_minuten=45,
            beschreibung="Persönliche Beratung in der Fahrschule",
            aktiv=True,
        )
        self.lehrer = Fahrlehrer.objects.create(
            name="Jonas Fahrlehrer",
            email="jonas@fahrschule-schaltwerk.de",
            bundesland="BE",
            aktiv=True,
        )
        morgen = timezone.localdate() + dt.timedelta(days=2)
        start = timezone.make_aware(dt.datetime.combine(morgen, dt.time(14, 0)))
        ende = timezone.make_aware(dt.datetime.combine(morgen, dt.time(14, 45)))
        self.termin = Termin.objects.create(
            fahrlehrer=self.lehrer,
            terminart=self.art,
            beginn=start,
            ende=ende,
            status=Termin.Status.FREI,
        )

    def test_vollstaendiger_buchungs_und_stornierungsablauf(self):
        # 1. Startseite aufrufen
        antwort_start = self.client.get(reverse("termine:start"))
        self.assertEqual(antwort_start.status_code, 200)
        self.assertContains(antwort_start, "schalti-header")
        self.assertContains(antwort_start, "Beratungstermin buchen")
        self.assertContains(antwort_start, reverse("termine:buchen", args=[self.termin.pk]))

        # 2. Buchungsformular aufrufen
        antwort_formular = self.client.get(reverse("termine:buchen", args=[self.termin.pk]))
        self.assertEqual(antwort_formular.status_code, 200)
        self.assertContains(antwort_formular, "Erstberatung Führerschein")
        self.assertContains(antwort_formular, 'name="name"')
        self.assertContains(antwort_formular, 'name="email"')
        self.assertContains(antwort_formular, 'name="telefon"')
        self.assertContains(antwort_formular, 'name="datenschutz"')

        # 3. Formular absenden (Reservierung)
        post_daten = {
            "name": "Erika Mustermann",
            "email": "erika@example.com",
            "telefon": "0170 12345678",
            "fuehrerscheinklasse": "B",
            "nachricht": "Ich möchte gern den Führerschein Klasse B machen.",
            "datenschutz": True,
            "website": "",  # Honeypot muss leer sein
        }
        antwort_reservierung = self.client.post(
            reverse("termine:buchen", args=[self.termin.pk]),
            data=post_daten,
        )
        self.assertEqual(antwort_reservierung.status_code, 302)

        buchung = Buchung.objects.get(termin=self.termin)
        self.assertEqual(buchung.name, "Erika Mustermann")
        self.assertEqual(buchung.email, "erika@example.com")
        self.assertEqual(buchung.status, Buchung.Status.OFFEN)
        self.termin.refresh_from_db()
        self.assertEqual(self.termin.status, Termin.Status.RESERVIERT)

        # Redirect auf der Webseite führt direkt zur Buchungsübersicht (Warte auf Bestätigung)
        kunden_url = reverse("termine:buchung", args=[buchung.token])
        self.assertRedirects(antwort_reservierung, kunden_url)

        # 4. Der Kunde öffnet den Link aus der Bestätigungs-E-Mail
        bestaetigen_url = reverse("termine:bestaetigen", args=[buchung.token])
        antwort_bestaetigen_seite = self.client.get(bestaetigen_url)
        self.assertEqual(antwort_bestaetigen_seite.status_code, 200)
        self.assertContains(antwort_bestaetigen_seite, "Nur noch bestätigen")
        self.assertContains(antwort_bestaetigen_seite, "Erstberatung Führerschein")

        # 5. Verbindlich per POST bestätigen (Schutz vor Mailscanner GET-Prefetches)
        antwort_bestaetigen = self.client.post(bestaetigen_url)
        self.assertEqual(antwort_bestaetigen.status_code, 200)
        self.assertContains(antwort_bestaetigen, "Termin bestätigt")

        buchung.refresh_from_db()
        self.termin.refresh_from_db()
        self.assertEqual(buchung.status, Buchung.Status.BESTAETIGT)
        self.assertEqual(self.termin.status, Termin.Status.GEBUCHT)

        # 6. Kundenansicht aufrufen
        antwort_kunde = self.client.get(kunden_url)
        self.assertEqual(antwort_kunde.status_code, 200)
        self.assertContains(antwort_kunde, "Ihr Termin")
        self.assertContains(antwort_kunde, "Erika Mustermann")
        self.assertContains(antwort_kunde, reverse("termine:buchung_stornieren", args=[buchung.token]))
        self.assertContains(antwort_kunde, reverse("termine:buchung_loeschen", args=[buchung.token]))

        # Gebuchter Termin darf nicht mehr frei auf der Startseite sein
        antwort_start_nachher = self.client.get(reverse("termine:start"))
        self.assertNotContains(antwort_start_nachher, reverse("termine:buchen", args=[self.termin.pk]))

        # 7. Stornierung durch den Kunden
        storno_url = reverse("termine:buchung_stornieren", args=[buchung.token])
        antwort_storno = self.client.post(storno_url)
        self.assertRedirects(antwort_storno, kunden_url)

        buchung.refresh_from_db()
        self.termin.refresh_from_db()
        self.assertEqual(buchung.status, Buchung.Status.STORNIERT)
        self.assertEqual(self.termin.status, Termin.Status.FREI)

        # Termin ist sofort wieder auf der Startseite buchbar!
        antwort_start_nach_storno = self.client.get(reverse("termine:start"))
        self.assertContains(antwort_start_nach_storno, reverse("termine:buchen", args=[self.termin.pk]))

        # 8. DSGVO-Löschung („Recht auf Vergessenwerden“)
        loesch_url = reverse("termine:buchung_loeschen", args=[buchung.token])
        antwort_loeschen = self.client.post(loesch_url)
        self.assertEqual(antwort_loeschen.status_code, 200)
        self.assertContains(antwort_loeschen, "Daten gelöscht")

        # Nach der Löschung liefert der geheime Token 404
        antwort_nach_loeschung = self.client.get(kunden_url)
        self.assertEqual(antwort_nach_loeschung.status_code, 404)
