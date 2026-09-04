"""Tests für den Slot-Generator: Rhythmus, Feiertage, Sperrzeiten, Idempotenz.

Der Generator arbeitet grundsätzlich nur in der Zukunft. Alle Testdaten werden
deshalb relativ zu „heute“ berechnet – feste Jahreszahlen würden die Suite
irgendwann von selbst rot werden lassen.
"""

import datetime as dt

from django.test import TestCase
from django.utils import timezone

from termine.models import Fahrlehrer, RhythmusRegel, Sperrzeit, Termin, Terminart
from termine.services.feiertage import feiertage_im_zeitraum, ist_feiertag
from termine.services.planung import (
    generiere_termine,
    lokal,
    slots_einer_regel,
    termine_manuell_anlegen,
)


def naechster_wochentag(wochentag: int, mindestens_tage_voraus: int = 7) -> dt.date:
    """Das nächste Vorkommen eines Wochentags (0 = Montag) in der Zukunft."""
    tag = timezone.localdate() + dt.timedelta(days=mindestens_tage_voraus)
    while tag.weekday() != wochentag:
        tag += dt.timedelta(days=1)
    return tag


def naechstes_datum(monat: int, tag: int, mindestens_tage_voraus: int = 10) -> dt.date:
    """Das nächste Vorkommen eines festen Kalendertags (z. B. 1. November)."""
    heute = timezone.localdate()
    jahr = heute.year
    while True:
        kandidat = dt.date(jahr, monat, tag)
        if kandidat >= heute + dt.timedelta(days=mindestens_tage_voraus):
            return kandidat
        jahr += 1


def termine_am(tag: dt.date, **filter):
    return Termin.objects.filter(
        beginn__gte=lokal(tag, dt.time.min), beginn__lte=lokal(tag, dt.time.max), **filter
    )


class BasisDaten(TestCase):
    """Ein Fahrlehrer in Baden-Württemberg mit einer 30-Minuten-Terminart."""

    def setUp(self):
        self.art = Terminart.objects.create(name="Beratung", dauer_minuten=30)
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Anna Berger",
            email="anna@example.org",
            bundesland="BW",
            horizont_wochen=4,
            vorlauf_stunden=0,
        )
        self.montag = naechster_wochentag(0)

    def regel(self, **felder):
        vorgaben = {
            "fahrlehrer": self.fahrlehrer,
            "terminart": self.art,
            "wochentage": [0],  # Montag
            "beginn": dt.time(9, 0),
            "ende": dt.time(12, 0),
            "gueltig_ab": timezone.localdate() - dt.timedelta(days=365),
        }
        vorgaben.update(felder)
        return RhythmusRegel.objects.create(**vorgaben)


class SlotAufteilung(BasisDaten):
    def test_fenster_wird_in_termine_zerlegt(self):
        slots = slots_einer_regel(self.regel(), self.montag)
        self.assertEqual(len(slots), 6)  # 9:00–12:00 zu je 30 Minuten
        self.assertEqual(timezone.localtime(slots[0][0]).time(), dt.time(9, 0))
        self.assertEqual(timezone.localtime(slots[-1][1]).time(), dt.time(12, 0))

    def test_puffer_vergroessert_den_abstand(self):
        self.art.puffer_minuten = 15
        self.art.save()
        slots = slots_einer_regel(self.regel(), self.montag)
        self.assertEqual(len(slots), 4)  # 45 Minuten Schrittweite
        self.assertEqual(timezone.localtime(slots[1][0]).time(), dt.time(9, 45))

    def test_angebrochener_rest_faellt_weg(self):
        regel = self.regel(beginn=dt.time(9, 0), ende=dt.time(10, 20))
        slots = slots_einer_regel(regel, self.montag)
        self.assertEqual(len(slots), 2)  # 9:00 und 9:30, der Rest passt nicht mehr


class RhythmusLogik(BasisDaten):
    def test_nur_die_gewaehlten_wochentage(self):
        regel = self.regel(wochentage=[1, 3])  # Dienstag, Donnerstag
        self.assertTrue(regel.gilt_am(self.montag + dt.timedelta(days=1)))
        self.assertTrue(regel.gilt_am(self.montag + dt.timedelta(days=3)))
        self.assertFalse(regel.gilt_am(self.montag))
        self.assertFalse(regel.gilt_am(self.montag + dt.timedelta(days=2)))

    def test_zweiwochenrhythmus_greift_jede_zweite_woche(self):
        regel = self.regel(intervall_wochen=2, referenzwoche=self.montag)
        for wochen, erwartet in ((0, True), (1, False), (2, True), (3, False), (4, True)):
            with self.subTest(wochen=wochen):
                tag = self.montag + dt.timedelta(days=7 * wochen)
                self.assertEqual(regel.gilt_am(tag), erwartet)

    def test_dreiwochenrhythmus(self):
        regel = self.regel(intervall_wochen=3, referenzwoche=self.montag)
        for wochen, erwartet in ((0, True), (1, False), (2, False), (3, True), (4, False)):
            with self.subTest(wochen=wochen):
                tag = self.montag + dt.timedelta(days=7 * wochen)
                self.assertEqual(regel.gilt_am(tag), erwartet)

    def test_referenzwoche_verschiebt_den_rhythmus(self):
        """Dieselbe Regel, aber die Referenzwoche liegt eine Woche später."""
        regel = self.regel(
            intervall_wochen=2, referenzwoche=self.montag + dt.timedelta(days=7)
        )
        self.assertFalse(regel.gilt_am(self.montag))
        self.assertTrue(regel.gilt_am(self.montag + dt.timedelta(days=7)))

    def test_gueltigkeitszeitraum_wird_beachtet(self):
        regel = self.regel(
            gueltig_ab=self.montag,
            gueltig_bis=self.montag + dt.timedelta(days=13),
        )
        self.assertFalse(regel.gilt_am(self.montag - dt.timedelta(days=7)))
        self.assertTrue(regel.gilt_am(self.montag))
        self.assertTrue(regel.gilt_am(self.montag + dt.timedelta(days=7)))
        self.assertFalse(regel.gilt_am(self.montag + dt.timedelta(days=14)))

    def test_inaktive_regel_greift_nie(self):
        self.assertFalse(self.regel(aktiv=False).gilt_am(self.montag))


class FeiertagsLogik(BasisDaten):
    def test_bundeslaender_unterscheiden_sich(self):
        # Allerheiligen (1. November) ist in Baden-Württemberg Feiertag, in Berlin nicht.
        allerheiligen = naechstes_datum(11, 1)
        self.assertTrue(ist_feiertag("BW", allerheiligen))
        self.assertFalse(ist_feiertag("BE", allerheiligen))

        # Der Tag der Deutschen Einheit (3. Oktober) gilt dagegen überall.
        einheit = naechstes_datum(10, 3)
        self.assertTrue(ist_feiertag("BW", einheit))
        self.assertTrue(ist_feiertag("BE", einheit))

    def test_feiertage_im_zeitraum_liefert_namen(self):
        einheit = naechstes_datum(10, 3)
        gefunden = feiertage_im_zeitraum(
            "BW", einheit - dt.timedelta(days=1), einheit + dt.timedelta(days=1)
        )
        self.assertEqual(gefunden.get(einheit), "Tag der Deutschen Einheit")

    def test_generator_laesst_feiertag_aus(self):
        feiertag = naechstes_datum(11, 1)  # Allerheiligen – in BW ein Feiertag
        self.regel(wochentage=[feiertag.weekday()], feiertage_auslassen=True)

        bericht = generiere_termine(
            self.fahrlehrer, wochen=3, ab=feiertag - dt.timedelta(days=2)
        )

        self.assertFalse(termine_am(feiertag).exists())
        self.assertGreater(bericht.feiertage_uebersprungen, 0)
        # Der gleiche Wochentag eine Woche später wurde ganz normal geplant.
        self.assertTrue(termine_am(feiertag + dt.timedelta(days=7)).exists())

    def test_feiertag_kann_bewusst_mitgeplant_werden(self):
        feiertag = naechstes_datum(11, 1)
        self.regel(wochentage=[feiertag.weekday()], feiertage_auslassen=False)

        generiere_termine(self.fahrlehrer, wochen=3, ab=feiertag - dt.timedelta(days=2))

        self.assertTrue(termine_am(feiertag).exists())

    def test_bundesland_des_fahrlehrers_entscheidet(self):
        """Gleicher Rhythmus, zwei Bundesländer – nur einer hat an dem Tag frei."""
        feiertag = naechstes_datum(11, 1)

        berliner = Fahrlehrer.objects.create(
            name="Tom Keller", email="tom@example.org", bundesland="BE", vorlauf_stunden=0
        )
        RhythmusRegel.objects.create(
            fahrlehrer=berliner,
            terminart=self.art,
            wochentage=[feiertag.weekday()],
            beginn=dt.time(9, 0),
            ende=dt.time(11, 0),
            gueltig_ab=timezone.localdate() - dt.timedelta(days=365),
        )
        self.regel(wochentage=[feiertag.weekday()])  # der Fahrlehrer aus BW

        ab = feiertag - dt.timedelta(days=2)
        generiere_termine(berliner, wochen=1, ab=ab)
        generiere_termine(self.fahrlehrer, wochen=1, ab=ab)

        self.assertTrue(termine_am(feiertag, fahrlehrer=berliner).exists())
        self.assertFalse(termine_am(feiertag, fahrlehrer=self.fahrlehrer).exists())


class GeneratorVerhalten(BasisDaten):
    def test_ist_idempotent(self):
        self.regel()
        erster = generiere_termine(self.fahrlehrer, wochen=4, ab=self.montag)
        anzahl = Termin.objects.count()

        zweiter = generiere_termine(self.fahrlehrer, wochen=4, ab=self.montag)

        self.assertGreater(erster.erstellt, 0)
        self.assertEqual(Termin.objects.count(), anzahl)
        self.assertEqual(zweiter.erstellt, 0)
        self.assertEqual(zweiter.entfernt, 0)
        self.assertEqual(zweiter.unveraendert, anzahl)

    def test_gebuchte_termine_werden_nie_geloescht(self):
        regel = self.regel()
        generiere_termine(self.fahrlehrer, wochen=4, ab=self.montag)
        termin = Termin.objects.order_by("beginn").first()
        termin.status = Termin.Status.GEBUCHT
        termin.save()

        # Die Regel wird auf einen ganz anderen Wochentag umgestellt.
        regel.wochentage = [2]
        regel.save()
        generiere_termine(self.fahrlehrer, wochen=4, ab=self.montag)

        termin.refresh_from_db()
        self.assertEqual(termin.status, Termin.Status.GEBUCHT)

    def test_reservierte_termine_werden_nie_geloescht(self):
        regel = self.regel()
        generiere_termine(self.fahrlehrer, wochen=4, ab=self.montag)
        termin = Termin.objects.order_by("beginn").first()
        termin.status = Termin.Status.RESERVIERT
        termin.save()

        regel.aktiv = False
        regel.save()
        generiere_termine(self.fahrlehrer, wochen=4, ab=self.montag)

        self.assertTrue(Termin.objects.filter(pk=termin.pk).exists())

    def test_freie_regel_termine_verschwinden_bei_regelaenderung(self):
        regel = self.regel()
        generiere_termine(self.fahrlehrer, wochen=4, ab=self.montag)
        vorher = Termin.objects.count()
        self.assertGreater(vorher, 0)

        regel.aktiv = False
        regel.save()
        bericht = generiere_termine(self.fahrlehrer, wochen=4, ab=self.montag)

        self.assertEqual(Termin.objects.count(), 0)
        self.assertEqual(bericht.entfernt, vorher)

    def test_manuelle_termine_bleiben_unangetastet(self):
        self.regel()
        neue, _ = termine_manuell_anlegen(
            self.fahrlehrer,
            self.art,
            self.montag + dt.timedelta(days=2),
            dt.time(16, 0),
            dt.time(17, 0),
        )
        self.assertEqual(len(neue), 2)

        generiere_termine(self.fahrlehrer, wochen=4, ab=self.montag)

        self.assertEqual(Termin.objects.filter(herkunft=Termin.Herkunft.MANUELL).count(), 2)

    def test_sperrzeit_verhindert_termine(self):
        self.regel()
        gesperrt = self.montag + dt.timedelta(days=7)
        Sperrzeit.objects.create(
            fahrlehrer=self.fahrlehrer,
            beginn=lokal(gesperrt, dt.time.min),
            ende=lokal(gesperrt, dt.time.max),
            grund="Urlaub",
        )

        bericht = generiere_termine(self.fahrlehrer, wochen=4, ab=self.montag)

        self.assertFalse(termine_am(gesperrt).exists())
        self.assertGreater(bericht.sperrzeiten_uebersprungen, 0)
        self.assertTrue(termine_am(self.montag).exists())

    def test_ueberlappende_regeln_erzeugen_keine_doppelten_termine(self):
        self.regel(beginn=dt.time(9, 0), ende=dt.time(12, 0))
        self.regel(beginn=dt.time(10, 0), ende=dt.time(13, 0))

        generiere_termine(self.fahrlehrer, wochen=1, ab=self.montag)

        termine = list(termine_am(self.montag).order_by("beginn"))
        self.assertTrue(termine)
        for vorheriger, naechster in zip(termine, termine[1:]):
            self.assertLessEqual(vorheriger.ende, naechster.beginn)

    def test_vergangenheit_wird_nicht_angefasst(self):
        self.regel(wochentage=[0, 1, 2, 3, 4, 5, 6])
        vorbei = timezone.now() - dt.timedelta(days=3)
        vergangener = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.art,
            beginn=vorbei,
            ende=vorbei + dt.timedelta(minutes=30),
            status=Termin.Status.FREI,
            herkunft=Termin.Herkunft.REGEL,
        )

        generiere_termine(self.fahrlehrer, wochen=2)

        self.assertTrue(Termin.objects.filter(pk=vergangener.pk).exists())

    def test_horizont_begrenzt_die_planung(self):
        self.regel(wochentage=[0, 1, 2, 3, 4, 5, 6])
        generiere_termine(self.fahrlehrer, wochen=2)

        grenze = lokal(timezone.localdate() + dt.timedelta(days=14), dt.time.min)
        self.assertFalse(Termin.objects.filter(beginn__gte=grenze).exists())
        self.assertTrue(Termin.objects.exists())


class ManuelleTagesplanung(BasisDaten):
    def test_zeitfenster_wird_zerlegt(self):
        neue, uebersprungen = termine_manuell_anlegen(
            self.fahrlehrer,
            self.art,
            self.montag + dt.timedelta(days=3),
            dt.time(14, 0),
            dt.time(17, 0),
        )
        self.assertEqual(len(neue), 6)
        self.assertEqual(uebersprungen, 0)

    def test_belegte_zeitfenster_werden_uebersprungen(self):
        tag = self.montag + dt.timedelta(days=3)
        termine_manuell_anlegen(
            self.fahrlehrer, self.art, tag, dt.time(14, 0), dt.time(15, 0)
        )

        neue, uebersprungen = termine_manuell_anlegen(
            self.fahrlehrer, self.art, tag, dt.time(14, 0), dt.time(16, 0)
        )

        self.assertEqual(len(neue), 2)      # 15:00 und 15:30
        self.assertEqual(uebersprungen, 2)  # 14:00 und 14:30 waren schon belegt
        self.assertEqual(Termin.objects.count(), 4)


class SperrzeitRegelnTests(BasisDaten):
    def test_sperrzeit_regel_erzeugt_sperren_und_blockiert_angebot(self):
        # 1. Angebots-Regel 14:00 - 18:00
        self.regel(
            wochentage=[self.montag.weekday()],
            beginn=dt.time(14, 0),
            ende=dt.time(18, 0),
        )
        # 2. Blocker-Regel (z.B. Kita) 15:00 - 16:30
        RhythmusRegel.objects.create(
            fahrlehrer=self.fahrlehrer,
            regel_art=RhythmusRegel.RegelArt.SPERRE,
            sperrzeit_typ=Sperrzeit.Typ.PRIVAT,
            grund="Kita abholen",
            beginn=dt.time(15, 0),
            ende=dt.time(16, 30),
            wochentage=[self.montag.weekday()],
            gueltig_ab=self.montag,
            intervall_wochen=1,
            aktiv=True,
        )

        generiere_termine(self.fahrlehrer, wochen=2, ab=self.montag)
        # Sperrzeit muss existieren
        sperre = Sperrzeit.objects.filter(
            fahrlehrer=self.fahrlehrer,
            regel__isnull=False,
            typ=Sperrzeit.Typ.PRIVAT,
            grund="Kita abholen",
            beginn__date=self.montag,
        ).first()
        self.assertIsNotNone(sperre)
        self.assertEqual(sperre.beginn, lokal(self.montag, dt.time(15, 0)))
        self.assertEqual(sperre.ende, lokal(self.montag, dt.time(16, 30)))

        # Termine am Montag: 14:00, 14:30, (15:00-16:30 geblockt), 16:30, 17:00, 17:30
        termine = termine_am(self.montag)
        uhrzeiten = [timezone.localtime(t.beginn).strftime("%H:%M") for t in termine]
        self.assertIn("14:00", uhrzeiten)
        self.assertIn("14:30", uhrzeiten)
        self.assertNotIn("15:00", uhrzeiten)
        self.assertNotIn("15:30", uhrzeiten)
        self.assertNotIn("16:00", uhrzeiten)
        self.assertIn("16:30", uhrzeiten)
        self.assertIn("17:00", uhrzeiten)
        self.assertIn("17:30", uhrzeiten)

