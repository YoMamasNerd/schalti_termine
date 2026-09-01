"""Absicherung der Logik-Fixes rund um Buchung, Vorlauf und Handplanung."""

import datetime as dt
import hashlib

from django.test import TestCase
from django.utils import timezone

from termine.models import Buchung, Fahrlehrer, Sperrzeit, Termin, Terminart
from termine.services import buchung as buchungs_service
from termine.services.planung import lokal, termine_manuell_anlegen
from termine.tests.test_buchung import BuchungsBasis


class VerschiebenValidierung(BuchungsBasis):
    """Verschieben ist intern großzügig: Vorlauf, Horizont und Fahrlehrer-Grenze
    gelten nicht – nur FREI-Status und Sperrzeiten schützen den Ziel-Slot."""

    def test_verschieben_ignoriert_vorlauf(self):
        buchung = self.bestaetigen(self.reservieren())
        zu_kurz = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            beginn=timezone.now() + dt.timedelta(minutes=10),
            ende=timezone.now() + dt.timedelta(minutes=40),
            status=Termin.Status.FREI,
        )

        self.mit_commit(buchungs_service.verschieben, buchung, zu_kurz.pk)

        buchung.refresh_from_db()
        self.assertEqual(buchung.termin_id, zu_kurz.pk)

    def test_verschieben_ignoriert_horizont(self):
        buchung = self.bestaetigen(self.reservieren())
        spaeter = self.neuer_termin(tage_voraus=400)

        self.mit_commit(buchungs_service.verschieben, buchung, spaeter.pk)

        buchung.refresh_from_db()
        self.assertEqual(buchung.termin_id, spaeter.pk)

    def test_verschieben_ablehnung_gesperrt(self):
        buchung = self.bestaetigen(self.reservieren())
        ziel = self.neuer_termin(tage_voraus=6)
        Sperrzeit.objects.create(
            fahrlehrer=self.fahrlehrer,
            beginn=ziel.beginn - dt.timedelta(hours=1),
            ende=ziel.ende + dt.timedelta(hours=1),
        )

        with self.assertRaises(buchungs_service.TerminNichtVerfuegbar):
            buchungs_service.verschieben(buchung, ziel.pk)

        buchung.refresh_from_db()
        self.assertEqual(buchung.termin_id, self.termin.pk)

    def test_verschieben_auf_fremden_fahrlehrer_als_vertretung(self):
        buchung = self.bestaetigen(self.reservieren())
        tom = Fahrlehrer.objects.create(
            name="Tom Keller", email="tom@example.org", bundesland="BE", vorlauf_stunden=1
        )
        beginn = (timezone.now() + dt.timedelta(days=5)).replace(
            hour=11, minute=0, second=0, microsecond=0
        )
        fremd = Termin.objects.create(
            fahrlehrer=tom,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
            status=Termin.Status.FREI,
        )

        self.mit_commit(buchungs_service.verschieben, buchung, fremd.pk)

        buchung.refresh_from_db()
        fremd.refresh_from_db()
        self.assertEqual(buchung.termin_id, fremd.pk)
        self.assertEqual(fremd.status, Termin.Status.GEBUCHT)

    def test_verschieben_auf_gueltigen_termin_funktioniert(self):
        buchung = self.bestaetigen(self.reservieren())
        ziel = self.neuer_termin(tage_voraus=6, stunde=14)

        self.mit_commit(buchungs_service.verschieben, buchung, ziel.pk)

        buchung.refresh_from_db()
        ziel.refresh_from_db()
        self.assertEqual(buchung.termin_id, ziel.pk)
        self.assertEqual(ziel.status, Termin.Status.GEBUCHT)


class AblaufVergangenerTermine(BuchungsBasis):
    def test_abgelaufene_reservierung_vergangen_bleibt_nicht_frei(self):
        termin = self.neuer_termin(stunde=13)
        termin.beginn = timezone.now() - dt.timedelta(hours=2)
        termin.ende = termin.beginn + dt.timedelta(minutes=30)
        termin.status = Termin.Status.RESERVIERT
        termin.save()
        buchung = Buchung.objects.create(
            termin=termin,
            name="Max Muster",
            email="max@example.org",
            status=Buchung.Status.OFFEN,
            reserviert_bis=timezone.now() - dt.timedelta(minutes=5),
        )

        anzahl = buchungs_service.abgelaufene_reservierungen_freigeben()

        buchung.refresh_from_db()
        termin.refresh_from_db()
        self.assertEqual(anzahl, 1)
        self.assertEqual(buchung.status, Buchung.Status.VERFALLEN)
        self.assertNotEqual(termin.status, Termin.Status.FREI)


class EmailHashPflege(TestCase):
    def setUp(self):
        self.art = Terminart.objects.create(name="Erstberatung", dauer_minuten=30)
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW"
        )
        beginn = (timezone.now() + dt.timedelta(days=3)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        self.termin = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )

    @staticmethod
    def _hash(email: str) -> str:
        return hashlib.sha256(email.lower().strip().encode("utf-8")).hexdigest()

    def test_email_hash_wird_bei_email_aenderung_aktualisiert(self):
        buchung = Buchung.objects.create(
            termin=self.termin, name="Max Muster", email="alt@example.org"
        )
        self.assertEqual(buchung.email_hash, self._hash("alt@example.org"))

        buchung.email = "neu@example.org"
        buchung.save()

        self.assertEqual(buchung.email_hash, self._hash("neu@example.org"))

    def test_email_hash_bleibt_bei_anonymisierung(self):
        buchung = Buchung.objects.create(
            termin=self.termin, name="Max Muster", email="max@example.org"
        )
        urspruenglicher_hash = buchung.email_hash

        # So macht es alte_buchungen_anonymisieren(): bulk-update ohne save().
        Buchung.objects.filter(pk=buchung.pk).update(
            name="Gelöscht", email="", anonymisiert_am=timezone.now()
        )

        buchung.refresh_from_db()
        self.assertEqual(buchung.email_hash, urspruenglicher_hash)

        # Ein explizites save() mit anonymisiertem Datensatz rührt den Hash
        # nicht an – sonst ginge das Historien-Matching verloren.
        buchung.telefon = "0123"
        buchung.save()
        buchung.refresh_from_db()
        self.assertEqual(buchung.email_hash, urspruenglicher_hash)

    def test_email_hash_bleibt_bei_name_geloescht(self):
        # daten_loeschen() lädt den Datensatz frisch aus der Datenbank (Name
        # "Gelöscht", E-Mail echt) und gibt ihn zurück. Würde ein späteres
        # save() darauf den Hash neu rechnen, ginge das Matching verloren.
        buchung = Buchung.objects.create(
            termin=self.termin, name="Erika Muster", email="erika@example.org",
            status=Buchung.Status.STORNIERT,
        )
        alter_hash = buchung.email_hash
        buchung.name = "Gelöscht"
        buchung.save()
        buchung.refresh_from_db()
        self.assertEqual(buchung.email_hash, alter_hash)


class FahrlehrerVorlauf(BuchungsBasis):
    def test_fahrlehrer_vorlauf_gewinnt_ueber_globale_einstellung(self):
        self.fahrlehrer.vorlauf_stunden = 72
        self.fahrlehrer.save()

        fruehestens = self.fahrlehrer.fruehester_start()
        self.assertGreaterEqual(fruehestens, timezone.now() + dt.timedelta(hours=71))

        in_2h = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            beginn=timezone.now() + dt.timedelta(hours=2),
            ende=timezone.now() + dt.timedelta(hours=2, minutes=30),
            status=Termin.Status.FREI,
        )
        with self.assertRaises(buchungs_service.TerminNichtVerfuegbar):
            self.reservieren(termin=in_2h)


class ManuellAnlegenMitSperrzeit(TestCase):
    def setUp(self):
        self.art = Terminart.objects.create(name="Erstberatung", dauer_minuten=30)
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )

    def test_manuell_anlegen_respektiert_sperrzeiten(self):
        morgen = timezone.localdate() + dt.timedelta(days=1)
        Sperrzeit.objects.create(
            fahrlehrer=self.fahrlehrer,
            beginn=lokal(morgen, dt.time(10, 0)),
            ende=lokal(morgen, dt.time(11, 0)),
        )

        neue, uebersprungen = termine_manuell_anlegen(
            self.fahrlehrer, self.art, morgen, dt.time(9, 0), dt.time(12, 0)
        )

        # 9:00, 9:30, 11:00 und 11:30 bleiben; 10:00 und 10:30 kollidieren.
        self.assertEqual(len(neue), 4)
        self.assertEqual(uebersprungen, 2)
        for termin in neue:
            self.assertFalse(
                termin.beginn < lokal(morgen, dt.time(11, 0))
                and termin.ende > lokal(morgen, dt.time(10, 0))
            )
