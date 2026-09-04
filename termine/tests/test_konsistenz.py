"""Befunde einer Konsistenzprüfung – festgehalten, damit sie nicht zurückkommen.

Jeder Test hier belegt genau eine Stelle, an der zwei Wege durch dieselbe
Frage vorher verschiedene Antworten gaben.
"""

from __future__ import annotations

import datetime as dt
import sys
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail as django_mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.forms import GlobaleEinstellungenForm, SperrzeitForm
from termine.models import (
    Buchung,
    Fahrlehrer,
    FahrschulEinstellungen,
    Fuehrerscheinklasse,
    RhythmusRegel,
    Sperrzeit,
    Termin,
    Terminart,
)
from termine.services import buchung as buchungs_service
from termine.services.planung import finde_kollisionen_rhythmus_regeln
from termine.tests.test_buchung import BuchungsBasis


class AbgeschalteteTerminartIstNichtBuchbar(BuchungsBasis):
    """`buchbar()` filtert `terminart__aktiv` – die Buchungswege müssen mit.

    Sonst wäre der Haken „Aktiv“ nur eine Empfehlung an die Übersicht: Wer die
    Adresse eines Termins kennt (aus einem Lesezeichen, einem alten Link),
    käme weiterhin ans Formular und durch bis zur Reservierung.
    """

    def setUp(self):
        super().setUp()
        self.art.aktiv = False
        self.art.save(update_fields=["aktiv"])

    def test_uebersicht_bietet_ihn_nicht_mehr_an(self):
        self.assertEqual(Termin.objects.buchbar().count(), 0)

    def test_formular_ueber_die_adresse_antwortet_mit_409(self):
        antwort = self.client.get(reverse("termine:buchen", args=[self.termin.pk]))
        self.assertEqual(antwort.status_code, 409)

    def test_reservieren_verweigert(self):
        with self.assertRaises(buchungs_service.TerminNichtVerfuegbar):
            buchungs_service.reservieren(
                self.termin.pk, name="Max Muster", email="max@example.org"
            )
        self.assertFalse(Buchung.objects.exists())


class LoeschenAufKundenwunschErreichtDenKunden(BuchungsBasis):
    """Die Absage muss an die Adresse gehen, die es gleich nicht mehr gibt.

    Im Betrieb laufen Mails über die Warteschlange, und der Worker lädt die
    Buchung anhand ihrer Nummer neu – aus einer Datenbank, in der dann schon
    „Gelöscht“ steht. Deshalb wird beim Löschen direkt verschickt.
    """

    def test_storno_mails_tragen_noch_die_alte_adresse(self):
        """Ausdrücklich auf dem Betriebsweg geprüft.

        Im Testlauf verschickt `mail` sonst direkt und die Warteschlange käme
        nie vor – gerade sie ist aber die Stelle, an der die Adresse verloren
        ging. `IM_TESTLAUF=False` plus ein Doppel für `async_task`, das tut,
        was der Worker tut: die Buchung anhand ihrer Nummer neu laden.
        """
        from termine.services import mail as mail_service

        buchung = self.bestaetigen(self.reservieren())
        django_mail.outbox.clear()

        def wie_der_worker(pfad, *args, **kwargs):
            self.assertEqual(pfad, "termine.services.mail._sende_mail_task")
            return mail_service._sende_mail_task(*args, **kwargs)

        with mock.patch.dict(sys.modules, {"django_q.tasks": mock.Mock(async_task=wie_der_worker)}):
            with self.settings(IM_TESTLAUF=False):
                self.mit_commit(buchungs_service.daten_loeschen, buchung)

        empfaenger = [adresse for nachricht in django_mail.outbox for adresse in nachricht.to]
        self.assertIn("max@example.org", empfaenger)
        self.assertIn(self.fahrlehrer.email, empfaenger)
        self.assertNotIn("", empfaenger)

    def test_daten_sind_danach_weg(self):
        buchung = self.bestaetigen(self.reservieren())
        self.mit_commit(buchungs_service.daten_loeschen, buchung)

        buchung.refresh_from_db()
        self.assertEqual(buchung.name, "Gelöscht")
        self.assertEqual(buchung.email, "")
        self.assertIsNotNone(buchung.anonymisiert_am)


class MeldungNachDemVerschieben(BuchungsBasis):
    """Die Erfolgsmeldung muss den neuen Termin nennen, nicht den alten."""

    def test_meldung_nennt_den_neuen_termin(self):
        buchung = self.bestaetigen(self.reservieren())
        ziel = self.neuer_termin(tage_voraus=9, stunde=14)
        chef = get_user_model().objects.create_user(
            "chefin", password="geheim123", is_staff=True
        )
        self.client.force_login(chef)

        with self.captureOnCommitCallbacks(execute=True):
            antwort = self.client.post(
                reverse("termine:buchung_verschieben", args=[buchung.pk]),
                {"neuer_termin_id": ziel.pk},
                follow=True,
            )

        meldungen = " ".join(str(m) for m in antwort.context["messages"])
        self.assertIn(f"{timezone.localtime(ziel.beginn):%H:%M}", meldungen)
        self.assertNotIn(f"{timezone.localtime(self.termin.beginn):%H:%M}", meldungen)


class SperrzeitTypIstImmerGueltig(TestCase):
    """Das Formularfeld ist freiwillig und liefert dann "" – kein gültiger Wert."""

    def setUp(self):
        self.fahrlehrer = Fahrlehrer.objects.create(name="Anna", email="a@example.org")
        self.chefin = get_user_model().objects.create_user(
            "chefin", password="geheim123", is_staff=True
        )
        self.client.force_login(self.chefin)

    def test_formular_liefert_ohne_angabe_einen_leeren_wert(self):
        form = SperrzeitForm(
            data={
                "fahrlehrer": self.fahrlehrer.pk,
                "von_tag": "2099-01-01",
                "bis_tag": "2099-01-02",
                "grund": "Urlaub",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["typ"], "")

    def test_gespeichert_wird_trotzdem_ein_gueltiger_typ(self):
        self.client.post(
            reverse("termine:sperrzeit_anlegen"),
            {
                "fahrlehrer": self.fahrlehrer.pk,
                "von_tag": "2099-01-01",
                "bis_tag": "2099-01-02",
                "grund": "Urlaub",
            },
        )
        sperre = Sperrzeit.objects.get()
        self.assertEqual(sperre.typ, Sperrzeit.Typ.SONSTIGE)
        self.assertIn(sperre.typ, dict(Sperrzeit.Typ.choices))


class DashboardZeigtNurAktiveBuchungen(BuchungsBasis):
    """Verfallen ist nicht aktiv – der Slot ist wieder frei und namenlos.

    `Termin.aktive_buchung` zählt nur OFFEN und BESTÄTIGT; das Dashboard muss
    dieselbe Grenze ziehen, sonst klebt der Name einer nie zustande gekommenen
    Reservierung am freien Termin.
    """

    def test_verfallene_reservierung_erscheint_nicht_am_termin(self):
        buchung = self.reservieren()
        buchung.status = Buchung.Status.VERFALLEN
        buchung.save(update_fields=["status"])
        Termin.objects.filter(pk=self.termin.pk).update(status=Termin.Status.FREI)

        chef = get_user_model().objects.create_user(
            "chefin", password="geheim123", is_staff=True
        )
        self.client.force_login(chef)
        tag = timezone.localtime(self.termin.beginn).date()
        antwort = self.client.get(
            reverse("termine:dashboard"), {"tag": tag.isoformat(), "monat": f"{tag:%Y-%m}"}
        )

        eintraege = [e for e in antwort.context["tages_termine"] if e["termin"].pk == self.termin.pk]
        self.assertEqual(len(eintraege), 1)
        self.assertIsNone(eintraege[0]["buchung"])
        self.assertIsNone(self.termin.aktive_buchung)


class KollisionenNurAusAngebotsRegeln(TestCase):
    """Eine Sperr-Regel *soll* blockieren – sie ist keine Kollision.

    Trüge sie versehentlich eine Terminart, meldete die Prüfung sie als
    Konflikt mit genau der Sperrzeit, die sie selbst erzeugt hat.
    """

    def test_sperr_regel_meldet_sich_nicht_selbst(self):
        fahrlehrer = Fahrlehrer.objects.create(name="Anna", email="a@example.org")
        art = Terminart.objects.create(name="Beratung", dauer_minuten=60)
        heute = timezone.localdate()
        morgen = heute + dt.timedelta(days=1)

        regel = RhythmusRegel.objects.create(
            fahrlehrer=fahrlehrer,
            regel_art=RhythmusRegel.RegelArt.SPERRE,
            terminart=art,
            wochentage=list(range(7)),
            beginn=dt.time(10, 0),
            ende=dt.time(12, 0),
            gueltig_ab=heute,
            grund="Kita",
        )
        Sperrzeit.objects.create(
            fahrlehrer=fahrlehrer,
            regel=regel,
            beginn=timezone.make_aware(dt.datetime.combine(morgen, dt.time(10, 0))),
            ende=timezone.make_aware(dt.datetime.combine(morgen, dt.time(12, 0))),
            grund="Kita",
        )

        self.assertEqual(
            finde_kollisionen_rhythmus_regeln([fahrlehrer], von=morgen, bis=morgen), []
        )


class KlassenAuswahlAusEinerQuelle(TestCase):
    """Einstellungen und Buchungsformular müssen dieselben Klassen kennen.

    Sonst ist eine unter /intern/klassen/ angelegte eigene Klasse nicht
    ankreuzbar – und fällt, sobald überhaupt gefiltert wird, aus dem
    Buchungsformular heraus, obwohl sie dort angeboten wurde.
    """

    def test_eigene_klasse_ist_in_den_einstellungen_waehlbar(self):
        Fuehrerscheinklasse.objects.create(code="B", name="PKW", reihenfolge=0)
        Fuehrerscheinklasse.objects.create(code="B78", name="PKW Sonderfall", reihenfolge=1)

        form = GlobaleEinstellungenForm(instance=FahrschulEinstellungen.get_solo())
        codes = [code for code, _ in form.fields["aktive_fuehrerscheinklassen"].choices]

        self.assertIn("B78", codes)

    def test_gefilterte_auswahl_bleibt_im_buchungsformular_erhalten(self):
        from termine.forms import BuchungsForm

        Fuehrerscheinklasse.objects.create(code="B", name="PKW", reihenfolge=0)
        Fuehrerscheinklasse.objects.create(code="B78", name="PKW Sonderfall", reihenfolge=1)
        einst = FahrschulEinstellungen.get_solo()
        einst.aktive_fuehrerscheinklassen = ["B78"]
        einst.save(update_fields=["aktive_fuehrerscheinklassen"])

        art = Terminart.objects.create(name="Beratung", dauer_minuten=60)
        codes = [code for code, _ in BuchungsForm(terminart=art).fields["fuehrerscheinklasse"].choices]

        self.assertIn("B78", codes)
        self.assertNotIn("B", codes)


class HorizontIstEineZahl(TestCase):
    """Der Planungshorizont gilt fahrschulweit – die Seite darf nichts anderes sagen."""

    def test_tagesplanung_nennt_den_wirksamen_horizont(self):
        fahrlehrer = Fahrlehrer.objects.create(
            name="Anna", email="a@example.org", horizont_wochen=99
        )
        einst = FahrschulEinstellungen.get_solo()
        einst.horizont_wochen = 6
        einst.save(update_fields=["horizont_wochen"])
        Terminart.objects.create(name="Beratung", dauer_minuten=60)

        chef = get_user_model().objects.create_user(
            "chefin", password="geheim123", is_staff=True
        )
        self.client.force_login(chef)
        antwort = self.client.get(reverse("termine:tagesplanung"))

        self.assertEqual(antwort.context["horizont_wochen"], 6)
        self.assertContains(antwort, "nächsten 6 Wochen")
        self.assertNotContains(antwort, "nächsten 99 Wochen")
        # Und dieselbe Zahl begrenzt tatsächlich die Buchung.
        letzter = timezone.localdate() + dt.timedelta(days=6 * 7 - 1)
        self.assertEqual(timezone.localtime(fahrlehrer.spaetester_start()).date(), letzter)


class FsmStornoErstNachDemCommit(BuchungsBasis):
    """Beim Verschieben muss der alte FSM-Eintrag nach dem Commit aufgelöst werden.

    Lief der Auftrag noch in der offenen Transaktion, las er den alten Termin
    als gebucht und löschte den FSM-Eintrag, statt ihn wieder auf „frei“ zu
    setzen – der wieder freigegebene Slot verlor damit seinen Blocker.
    """

    def test_alter_termin_wird_erst_nach_dem_commit_aufgeloest(self):
        buchung = self.bestaetigen(self.reservieren())
        ziel = self.neuer_termin(tage_voraus=9, stunde=14)
        alter_termin_pk = buchung.termin_id

        with mock.patch(
            "termine.services.fsm_sync.async_storniere_termin_in_fsm"
        ) as storno, mock.patch("termine.services.fsm_sync.async_buche_in_fsm") as buchen:
            with self.captureOnCommitCallbacks(execute=True) as rueckrufe:
                buchungs_service.verschieben(buchung, ziel.pk)
                # Während die Transaktion offen ist, darf noch nichts laufen.
                storno.assert_not_called()

        self.assertTrue(rueckrufe)
        storno.assert_called_once()
        self.assertEqual(storno.call_args[0][0].pk, alter_termin_pk)
        buchen.assert_called_once()
