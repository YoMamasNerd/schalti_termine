"""Tests für die deutsche Schreibweise von Datum und Uhrzeit.

Die App richtet sich an eine deutsche Fahrschule, also gilt überall
Tag.Monat.Jahr mit führender Null und die 24-Stunden-Uhr. Das steckte bisher
als handgeschriebene Formatangabe in jeder Vorlage – mit dem Fehler, dass
`j.m.Y` den Tag ohne, den Monat aber mit führender Null setzt: aus dem
5. September wurde „5.09.2026".

Geprüft wird deshalb mit einem einstelligen Tag und einer Uhrzeit am
Nachmittag – die beiden Fälle, in denen sich eine falsche Formatangabe
überhaupt zeigt.
"""

from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models import Fahrlehrer, RhythmusRegel, Termin, Terminart
from termine.services import buchung as buchungs_service


def naechster_einstelliger_tag() -> dt.date:
    """Ein Tag in der Zukunft, dessen Tageszahl einstellig ist."""
    tag = timezone.localdate() + dt.timedelta(days=1)
    while tag.day > 9:
        tag += dt.timedelta(days=1)
    return tag


class DeutscheSchreibweise(TestCase):
    def setUp(self):
        self.chef = get_user_model().objects.create_superuser("chef", password="geheim123")
        self.client.force_login(self.chef)
        self.art = Terminart.objects.create(name="Erstberatung", dauer_minuten=30)
        self.anna = Fahrlehrer.objects.create(
            name="Anna Berger", email="anna@example.org", bundesland="BW", vorlauf_stunden=0
        )
        self.tag = naechster_einstelliger_tag()

    def erwartet(self, tag: dt.date) -> str:
        return f"{tag.day:02d}.{tag.month:02d}.{tag.year}"

    def termin_am_nachmittag(self) -> Termin:
        beginn = timezone.make_aware(dt.datetime.combine(self.tag, dt.time(14, 30)))
        return Termin.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            beginn=beginn,
            ende=beginn + dt.timedelta(minutes=30),
        )

    def test_buchungsliste_schreibt_den_tag_mit_fuehrender_null(self):
        termin = self.termin_am_nachmittag()
        buchung = buchungs_service.reservieren(
            termin.pk, name="Lena Hoffmann", email="lena@example.org"
        )
        buchungs_service.bestaetigen(buchung)

        antwort = self.client.get(reverse("termine:buchungen"))
        self.assertContains(antwort, self.erwartet(self.tag))

    def test_buchungsliste_nutzt_die_vierundzwanzig_stunden_uhr(self):
        termin = self.termin_am_nachmittag()
        buchung = buchungs_service.reservieren(
            termin.pk, name="Lena Hoffmann", email="lena@example.org"
        )
        buchungs_service.bestaetigen(buchung)

        antwort = self.client.get(reverse("termine:buchungen"))
        self.assertContains(antwort, "14:30")
        self.assertNotContains(antwort, "02:30 PM")

    def test_regelliste_schreibt_den_tag_mit_fuehrender_null(self):
        RhythmusRegel.objects.create(
            fahrlehrer=self.anna,
            terminart=self.art,
            wochentage=[1],
            beginn=dt.time(14, 0),
            ende=dt.time(17, 0),
            gueltig_ab=self.tag,
        )
        antwort = self.client.get(reverse("termine:regeln"))
        self.assertContains(antwort, self.erwartet(self.tag))

    def test_formularfelder_liefern_das_datum_in_iso(self):
        # Der Browser zeigt das Feld in seiner eigenen Sprache an, erwartet im
        # value-Attribut aber ISO. Steht dort etwas anderes, bleibt das Feld
        # leer – deshalb ist gerade hier das deutsche Format falsch.
        antwort = self.client.get(reverse("termine:tagesplanung"))
        self.assertContains(antwort, f'value="{timezone.localdate():%Y-%m-%d}"')
        self.assertContains(antwort, 'type="date"')
