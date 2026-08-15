"""Tests für die FSM-Synchronisation und Blocker-Verwaltung."""

from __future__ import annotations

import datetime as dt
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from termine.models import Buchung, Fahrlehrer, Sperrzeit, Termin, Terminart
from termine.services import buchung as buchung_service
from termine.services.fsm_client import FsmError, FsmTermin
from termine.services.fsm_sync import (
    buche_in_fsm,
    storniere_in_fsm,
    sync_alle_fahrlehrer,
    sync_blocker_fuer_fahrlehrer,
)


class FsmSyncTests(TestCase):
    def setUp(self):
        super().setUp()
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Jonas Eisele",
            slug="jonas-eisele",
            email="jonas@example.com",
            fsm_id="fsm-lehrer-uuid-1",
            fsm_sync_aktiv=True,
            vorlauf_stunden=24,
            horizont_wochen=4,
        )
        self.terminart = Terminart.objects.create(
            name="Beratung",
            slug="beratung",
            dauer_minuten=30,
        )

    @override_settings(FSM_SYNC_ENABLED=False)
    def test_sync_ignoriert_wenn_global_deaktiviert(self):
        mock_client = MagicMock()
        anzahl = sync_blocker_fuer_fahrlehrer(self.fahrlehrer, client=mock_client)
        self.assertEqual(anzahl, 0)
        mock_client.get_termine.assert_not_called()

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_sync_ignoriert_wenn_fahrlehrer_ohne_fsm_id(self):
        self.fahrlehrer.fsm_id = ""
        self.fahrlehrer.save()

        mock_client = MagicMock()
        anzahl = sync_blocker_fuer_fahrlehrer(self.fahrlehrer, client=mock_client)
        self.assertEqual(anzahl, 0)
        mock_client.get_termine.assert_not_called()

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_sync_blocker_erstellt_sperrzeiten(self):
        jetzt = timezone.now()
        fsm_termine = [
            FsmTermin(
                id="fsm-termin-1",
                von=jetzt + dt.timedelta(days=2, hours=10),
                bis=jetzt + dt.timedelta(days=2, hours=11, minutes=30),
                fahrlehrer_id="fsm-lehrer-uuid-1",
                terminart="PS",
                titel="Fahrstunde Mustermann",
                schueler_name="Mustermann",
            ),
            FsmTermin(
                id="fsm-termin-2",
                von=jetzt + dt.timedelta(days=3, hours=14),
                bis=jetzt + dt.timedelta(days=3, hours=15),
                fahrlehrer_id="fsm-lehrer-uuid-1",
                terminart="PX",
                titel="Besprechung",
            ),
        ]

        mock_client = MagicMock()
        mock_client.get_termine.return_value = fsm_termine

        anzahl = sync_blocker_fuer_fahrlehrer(self.fahrlehrer, client=mock_client)
        self.assertEqual(anzahl, 2)

        sperren = list(Sperrzeit.objects.filter(fahrlehrer=self.fahrlehrer).order_by("beginn"))
        self.assertEqual(len(sperren), 2)
        self.assertEqual(sperren[0].fsm_id, "fsm-termin-1")
        self.assertIn("Mustermann", sperren[0].grund)
        self.assertEqual(sperren[1].fsm_id, "fsm-termin-2")

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_sync_blocker_ueberspringt_eigene_buchungen(self):
        jetzt = timezone.now()
        # Bereits von Schalti angelegter Termin
        Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.terminart,
            beginn=jetzt + dt.timedelta(days=2, hours=10),
            ende=jetzt + dt.timedelta(days=2, hours=10, minutes=30),
            fsm_termin_id="fsm-eigene-uuid",
        )

        fsm_termine = [
            FsmTermin(
                id="fsm-eigene-uuid",  # Eigener Termin
                von=jetzt + dt.timedelta(days=2, hours=10),
                bis=jetzt + dt.timedelta(days=2, hours=10, minutes=30),
                fahrlehrer_id="fsm-lehrer-uuid-1",
                terminart="PX",
                titel="Beratung Max",
            ),
            FsmTermin(
                id="fsm-fremder-termin",
                von=jetzt + dt.timedelta(days=3, hours=14),
                bis=jetzt + dt.timedelta(days=3, hours=15),
                fahrlehrer_id="fsm-lehrer-uuid-1",
                terminart="PS",
                titel="Fahrstunde",
            ),
        ]

        mock_client = MagicMock()
        mock_client.get_termine.return_value = fsm_termine

        anzahl = sync_blocker_fuer_fahrlehrer(self.fahrlehrer, client=mock_client)
        self.assertEqual(anzahl, 1)

        sperren = list(Sperrzeit.objects.filter(fahrlehrer=self.fahrlehrer))
        self.assertEqual(len(sperren), 1)
        self.assertEqual(sperren[0].fsm_id, "fsm-fremder-termin")

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_sync_blocker_entfernt_geloeschte_fsm_termine(self):
        jetzt = timezone.now()
        # Altes FSM-Event als Sperrzeit
        Sperrzeit.objects.create(
            fahrlehrer=self.fahrlehrer,
            fsm_id="fsm-altes-event",
            beginn=jetzt + dt.timedelta(days=2, hours=10),
            ende=jetzt + dt.timedelta(days=2, hours=11),
            grund="FSM: Alt",
            herkunft=Sperrzeit.Herkunft.FSM,
        )

        mock_client = MagicMock()
        mock_client.get_termine.return_value = []  # In FSM gelöscht

        anzahl = sync_blocker_fuer_fahrlehrer(self.fahrlehrer, client=mock_client)
        self.assertEqual(anzahl, 0)
        self.assertFalse(Sperrzeit.objects.filter(fsm_id="fsm-altes-event").exists())

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_buche_in_fsm_erfolg(self):
        jetzt = timezone.now()
        termin = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.terminart,
            beginn=jetzt + dt.timedelta(days=2, hours=10),
            ende=jetzt + dt.timedelta(days=2, hours=10, minutes=30),
            status=Termin.Status.FREI,
        )
        buchung = buchung_service.reservieren(
            termin.pk,
            name="Max Mustermann",
            email="max@example.com",
            telefon="0170123456",
        )

        mock_client = MagicMock()
        mock_client.termin_anlegen.return_value = "neue-fsm-id-123"

        fsm_id = buche_in_fsm(buchung, client=mock_client)
        self.assertEqual(fsm_id, "neue-fsm-id-123")

        termin.refresh_from_db()
        self.assertEqual(termin.fsm_termin_id, "neue-fsm-id-123")
        mock_client.termin_anlegen.assert_called_once()

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_storniere_in_fsm_erfolg(self):
        jetzt = timezone.now()
        termin = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.terminart,
            beginn=jetzt + dt.timedelta(days=2, hours=10),
            ende=jetzt + dt.timedelta(days=2, hours=10, minutes=30),
            status=Termin.Status.GEBUCHT,
            fsm_termin_id="zu-loeschende-fsm-id",
        )
        buchung = Buchung.objects.create(
            termin=termin,
            name="Max Mustermann",
            email="max@example.com",
            status=Buchung.Status.BESTAETIGT,
        )

        mock_client = MagicMock()
        mock_client.termin_loeschen.return_value = True

        erfolg = storniere_in_fsm(buchung, client=mock_client)
        self.assertTrue(erfolg)

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_exportiere_termin_nach_fsm_frei(self):
        jetzt = timezone.now()
        termin = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.terminart,
            beginn=jetzt + dt.timedelta(days=2, hours=10),
            ende=jetzt + dt.timedelta(days=2, hours=10, minutes=30),
            status=Termin.Status.FREI,
        )

        mock_client = MagicMock()
        mock_client.termin_anlegen.return_value = "fsm-placeholder-123"

        from termine.services.fsm_sync import exportiere_termin_nach_fsm

        fsm_id = exportiere_termin_nach_fsm(termin, client=mock_client)
        self.assertEqual(fsm_id, "fsm-placeholder-123")
        termin.refresh_from_db()
        self.assertEqual(termin.fsm_termin_id, "fsm-placeholder-123")
        self.assertIn("(frei)", mock_client.termin_anlegen.call_args[1]["titel"])

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_termine_entfernen_loescht_fsm_termin(self):
        from termine.services.planung import termine_entfernen

        jetzt = timezone.now()
        termin = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.terminart,
            beginn=jetzt + dt.timedelta(days=2, hours=10),
            ende=jetzt + dt.timedelta(days=2, hours=10, minutes=30),
            status=Termin.Status.FREI,
            fsm_termin_id="zu-loeschen-in-fsm",
        )

        mock_client = MagicMock()
        mock_client.termin_loeschen.return_value = True

        with patch("termine.services.fsm_sync.FsmClient", return_value=mock_client):
            termine_entfernen(Termin.objects.filter(pk=termin.pk))

        self.assertFalse(Termin.objects.filter(pk=termin.pk).exists())

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_sync_entfernt_freien_termin_wenn_in_fsm_geloescht(self):
        jetzt = timezone.now()
        termin = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.terminart,
            beginn=jetzt + dt.timedelta(days=2, hours=10),
            ende=jetzt + dt.timedelta(days=2, hours=10, minutes=30),
            status=Termin.Status.FREI,
            fsm_termin_id="in-fsm-nicht-mehr-da",
        )

        mock_client = MagicMock()
        mock_client.get_termine.return_value = []  # FSM liefert diesen Termin nicht mehr

        sync_blocker_fuer_fahrlehrer(self.fahrlehrer, client=mock_client)
        self.assertFalse(Termin.objects.filter(pk=termin.pk).exists())

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_management_command_fsm_sync(self):
        with self.subTest("Alle Fahrlehrer"):
            out = StringIO()
            mock_client = MagicMock()
            mock_client.get_termine.return_value = []

            call_command("fsm_sync", stdout=out)
            self.assertIn("Fertig:", out.getvalue())

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_stornierung_setzt_fsm_eintrag_auf_frei_zurueck(self):
        termin = Termin.objects.create(
            fahrlehrer=self.fahrlehrer,
            terminart=self.terminart,
            beginn=timezone.now() + dt.timedelta(days=2, hours=14),
            ende=timezone.now() + dt.timedelta(days=2, hours=14, minutes=30),
            status=Termin.Status.FREI,
            fsm_termin_id="fsm-blocker-123",
        )
        buchung = Buchung.objects.create(
            termin=termin,
            name="Max Mustermann",
            email="max@example.com",
            status=Buchung.Status.STORNIERT,
        )
        mock_client = MagicMock()
        mock_client.termin_aktualisieren.return_value = True

        storniere_in_fsm(buchung, client=mock_client)

        mock_client.termin_aktualisieren.assert_called_once_with(
            fsm_termin_id="fsm-blocker-123",
            fahrlehrer_fsm_id="fsm-lehrer-uuid-1",
            von=termin.beginn,
            bis=termin.ende,
            titel="Beratung: Beratung (frei)",
        )
        mock_client.termin_loeschen.assert_not_called()

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_strip_html_tags_in_fsm_sperrzeiten(self):
        jetzt = timezone.now()
        fsm_termine = [
            FsmTermin(
                id="fsm-html-1",
                von=jetzt + dt.timedelta(days=2, hours=10),
                bis=jetzt + dt.timedelta(days=2, hours=11),
                fahrlehrer_id="fsm-lehrer-uuid-1",
                terminart="PS",
                titel='<a href="schueler?id=123"><span class="alt-key">S -B Max Mustermann</span></a>',
            ),
        ]
        mock_client = MagicMock()
        mock_client.get_termine.return_value = fsm_termine

        sync_blocker_fuer_fahrlehrer(self.fahrlehrer, client=mock_client)

        sperre = Sperrzeit.objects.get(fsm_id="fsm-html-1")
        self.assertEqual(sperre.grund, "FSM: S -B Max Mustermann")


class FsmEinstellungenViewTests(TestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("admin", "admin@example.com", "pass")
        self.client.force_login(self.user)
        self.fahrlehrer = Fahrlehrer.objects.create(
            name="Jonas Eisele",
            slug="jonas-eisele",
            email="jonas@example.com",
            benutzer=self.user,
        )

    @override_settings(FSM_SYNC_ENABLED=False)
    def test_404_wenn_fsm_deaktiviert(self):
        res = self.client.get(reverse("termine:fsm_einstellungen"))
        self.assertEqual(res.status_code, 404)

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_laedt_wenn_fsm_aktiv(self):
        with patch("termine.staff_views.FsmClient") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.get_fahrlehrer.return_value = []
            mock_cls.return_value = mock_inst

            res = self.client.get(reverse("termine:fsm_einstellungen"))
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, "Fahrschulmanager (FSM)")
            self.assertContains(res, "Jonas Eisele")

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_speichert_fsm_zuordnungen(self):
        with patch("termine.staff_views.FsmClient") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.get_fahrlehrer.return_value = []
            mock_cls.return_value = mock_inst

            res = self.client.post(
                reverse("termine:fsm_einstellungen"),
                {
                    f"fsm_id_{self.fahrlehrer.pk}": "658688b4-eb51-418a-9811-bc5445281319",
                    f"fsm_sync_aktiv_{self.fahrlehrer.pk}": "on",
                },
            )
            self.assertRedirects(res, reverse("termine:fsm_einstellungen"))

            self.fahrlehrer.refresh_from_db()
            self.assertEqual(self.fahrlehrer.fsm_id, "658688b4-eb51-418a-9811-bc5445281319")
            self.assertTrue(self.fahrlehrer.fsm_sync_aktiv)

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_manueller_sync_button(self):
        with patch("termine.staff_views.FsmClient") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.get_termine.return_value = []
            mock_cls.return_value = mock_inst

            res = self.client.post(
                reverse("termine:fsm_einstellungen"),
                {"aktion": "sync"},
            )
            self.assertRedirects(res, reverse("termine:fsm_einstellungen"))

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_import_fahrlehrer_button(self):
        with patch("termine.staff_views.FsmClient") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.get_fahrlehrer.return_value = [
                {
                    "id": "fsm-lehrer-neu-123",
                    "vorname": "Stefan",
                    "nachname": "Richter",
                    "email": "stefan@example.com",
                    "mobil": "0171/123456",
                }
            ]
            mock_inst.get_termine.return_value = []
            mock_cls.return_value = mock_inst

            res = self.client.post(
                reverse("termine:fsm_einstellungen"),
                {"aktion": "import_fahrlehrer"},
            )
            self.assertRedirects(res, reverse("termine:fsm_einstellungen"))

            neuer_fl = Fahrlehrer.objects.get(fsm_id="fsm-lehrer-neu-123")
            self.assertEqual(neuer_fl.name, "Stefan Richter")
            self.assertTrue(neuer_fl.fsm_sync_aktiv)

    def test_zerlege_zeitraum_fuer_fsm(self):
        from termine.services.fsm_sync import zerlege_zeitraum_fuer_fsm
        from termine.services.planung import lokal

        tag = timezone.localdate()

        # 1. Konkrete Uhrzeit < 600 min an einem Tag (z.B. 4 Stunden = 240 min)
        t1 = lokal(tag, dt.time(10, 0))
        t2 = lokal(tag, dt.time(14, 0))
        chunks = zerlege_zeitraum_fuer_fsm(t1, t2, max_minuten=600)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], (t1, t2))

        # 2. Ganztägige Sperrzeit (00:00 bis 23:59:59) -> genau 1 Block von 08:00 bis 18:00 Uhr (600 min)
        tag_start = lokal(tag, dt.time.min)
        tag_ende = lokal(tag, dt.time.max)
        chunks = zerlege_zeitraum_fuer_fsm(tag_start, tag_ende, max_minuten=600)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][0].time(), dt.time(8, 0))
        self.assertEqual(chunks[0][1].time(), dt.time(18, 0))
        self.assertEqual((chunks[0][1] - chunks[0][0]).total_seconds() / 60, 600)

        # 3. Mehrtägiger Urlaub (3 volle Tage) -> genau 1 Block à 8:00-18:00 pro Tag = 3 Chunks
        ende_3_tage = lokal(tag + dt.timedelta(days=2), dt.time.max)
        chunks_3_tage = zerlege_zeitraum_fuer_fsm(tag_start, ende_3_tage, max_minuten=600)
        self.assertEqual(len(chunks_3_tage), 3)
        for start, ende in chunks_3_tage:
            self.assertEqual(start.time(), dt.time(8, 0))
            self.assertEqual(ende.time(), dt.time(18, 0))
            self.assertEqual((ende - start).total_seconds() / 60, 600)

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_exportiere_sperrzeit_nach_fsm(self):
        from termine.services.fsm_sync import exportiere_sperrzeit_nach_fsm

        self.fahrlehrer.fsm_id = "fsm-lehrer-uuid-1"
        self.fahrlehrer.fsm_sync_aktiv = True
        self.fahrlehrer.save()

        jetzt = timezone.now()
        sperre = Sperrzeit.objects.create(
            fahrlehrer=self.fahrlehrer,
            beginn=jetzt + dt.timedelta(days=5, hours=8),
            ende=jetzt + dt.timedelta(days=5, hours=16),
            grund="Urlaub",
            typ=Sperrzeit.Typ.SONSTIGE,
        )

        mock_client = MagicMock()
        mock_client.termin_anlegen.return_value = "fsm-sperre-id-123"

        ids = exportiere_sperrzeit_nach_fsm(sperre, client=mock_client)
        self.assertEqual(ids, ["fsm-sperre-id-123"])
        mock_client.termin_anlegen.assert_called_once()
        _, kwargs = mock_client.termin_anlegen.call_args
        self.assertEqual(kwargs["terminart"], "ST")
        self.assertIn("Sonstige Tätigkeit", kwargs["titel"])
        self.assertIn("Urlaub", kwargs["titel"])

        sperre.refresh_from_db()
        self.assertEqual(sperre.fsm_id, "fsm-sperre-id-123")

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_exportiere_sperrzeit_privat_nach_fsm(self):
        from termine.services.fsm_sync import exportiere_sperrzeit_nach_fsm

        self.fahrlehrer.fsm_id = "fsm-lehrer-uuid-1"
        self.fahrlehrer.fsm_sync_aktiv = True
        self.fahrlehrer.save()

        jetzt = timezone.now()
        sperre = Sperrzeit.objects.create(
            fahrlehrer=self.fahrlehrer,
            beginn=jetzt + dt.timedelta(days=2, hours=13),
            ende=jetzt + dt.timedelta(days=2, hours=15, minutes=30),
            grund="Kinder aus Kita abholen",
            typ=Sperrzeit.Typ.PRIVAT,
        )

        mock_client = MagicMock()
        mock_client.termin_anlegen.return_value = "fsm-privat-id-999"

        ids = exportiere_sperrzeit_nach_fsm(sperre, client=mock_client)
        self.assertEqual(ids, ["fsm-privat-id-999"])
        mock_client.termin_anlegen.assert_called_once()
        _, kwargs = mock_client.termin_anlegen.call_args
        self.assertEqual(kwargs["terminart"], "PP")
        self.assertIn("Kinder aus Kita abholen", kwargs["titel"])

    @override_settings(FSM_SYNC_ENABLED=False)
    def test_sperrzeit_sync_ignoriert_wenn_fsm_deaktiviert(self):
        from termine.services.fsm_sync import exportiere_sperrzeit_nach_fsm

        jetzt = timezone.now()
        sperre = Sperrzeit.objects.create(
            fahrlehrer=self.fahrlehrer,
            beginn=jetzt + dt.timedelta(days=5),
            ende=jetzt + dt.timedelta(days=6),
            grund="Urlaub",
        )

    def test_is_theorie_termin(self):
        from termine.services.fsm_sync import is_theorie_termin

        self.assertTrue(is_theorie_termin("PT", "TH-Grundstoff"))
        self.assertTrue(is_theorie_termin("TH", "Unterricht"))
        self.assertTrue(is_theorie_termin("PX", "Theorie Grundstoff Lektion 1"))
        self.assertTrue(is_theorie_termin("ST", "Zusatzstoff Motorrad"))
        self.assertFalse(is_theorie_termin("PS", "Fahrstunde Mustermann"))
        self.assertFalse(is_theorie_termin("PP", "PRIVAT!"))

    @override_settings(FSM_SYNC_ENABLED=True)
    def test_theorieunterricht_blockiert_alle_fahrlehrer(self):
        from termine.models import FahrschulEinstellungen
        from termine.services.fsm_sync import sync_alle_fahrlehrer

        # 2 Fahrlehrer
        einstellungen = FahrschulEinstellungen.get_solo()
        einstellungen.fsm_theorie_blockiert_beratung = True
        einstellungen.save()

        fl_anna = Fahrlehrer.objects.create(
            name="Anna Müller",
            slug="anna-mueller",
            fsm_id="fsm-anna-uuid",
            fsm_sync_aktiv=True,
        )
        fl_tom = Fahrlehrer.objects.create(
            name="Tom Schmidt",
            slug="tom-schmidt",
            fsm_id="fsm-tom-uuid",
            fsm_sync_aktiv=True,
        )

        jetzt = timezone.now()
        th_start = jetzt + dt.timedelta(days=1, hours=18)
        th_ende = jetzt + dt.timedelta(days=1, hours=19, minutes=30)

        # Freier Beratungstermin bei Tom im selben Zeitraum
        termin_tom = Termin.objects.create(
            fahrlehrer=fl_tom,
            terminart=Terminart.objects.create(name="Beratung", slug="beratung-th", dauer_minuten=45),
            beginn=th_start,
            ende=th_start + dt.timedelta(minutes=45),
            status=Termin.Status.FREI,
        )

        mock_client = MagicMock()
        mock_client.termin_anlegen.return_value = "fsm-mock-created-uuid"
        # Anna hat Theorieunterricht
        mock_client.get_termine.side_effect = lambda fl_id, s, e: [
            FsmTermin(
                id="fsm-th-101",
                von=th_start,
                bis=th_ende,
                fahrlehrer_id="fsm-anna-uuid",
                terminart="PT",
                titel="TH-Grundstoff Lektion 3",
            )
        ] if fl_id == "fsm-anna-uuid" else []

        sync_alle_fahrlehrer(client=mock_client)

        # Anna hat direkten FSM-Blocker
        self.assertTrue(Sperrzeit.objects.filter(fahrlehrer=fl_anna, fsm_id="fsm-th-101").exists())

        # Tom hat automatischen Raum-Blocker erhalten
        sperre_tom = Sperrzeit.objects.filter(fahrlehrer=fl_tom, fsm_id="theorie_fsm-th-101").first()
        self.assertIsNotNone(sperre_tom)
        self.assertIn("Theorieunterricht", sperre_tom.grund)
        self.assertEqual(sperre_tom.beginn, th_start)
        self.assertEqual(sperre_tom.ende, th_ende)

        # Toms freier Beratungstermin wurde entfernt
        self.assertFalse(Termin.objects.filter(pk=termin_tom.pk).exists())

        # Deaktivieren der Option bereinigt den Blocker für Tom beim nächsten Sync
        einstellungen.fsm_theorie_blockiert_beratung = False
        einstellungen.save()

        sync_alle_fahrlehrer(client=mock_client)
        self.assertFalse(Sperrzeit.objects.filter(fahrlehrer=fl_tom, fsm_id="theorie_fsm-th-101").exists())
        # Annas eigener Blocker bleibt
        self.assertTrue(Sperrzeit.objects.filter(fahrlehrer=fl_anna, fsm_id="fsm-th-101").exists())



