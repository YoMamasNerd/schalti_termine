from __future__ import annotations

import datetime as dt

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from .common import WOCHENTAG_KURZ
from .fahrlehrer import Fahrlehrer
from .terminart import Terminart


class SperrzeitTyp(models.TextChoices):
    SONSTIGE = "sonstige", "Sonstige Tätigkeit / Urlaub (Arbeitszeit)"
    PRIVAT = "privat", "Privat (keine Arbeitszeit)"


class RhythmusRegel(models.Model):
    """Wiederkehrende Verfügbarkeit, aus der Termine im Voraus erzeugt werden.

    Beispiel: „montags und mittwochs 14:00–18:00, jede Woche“ oder
    „freitags 09:00–12:00, jede 2. Woche“.
    """

    class RegelArt(models.TextChoices):
        ANGEBOT = "angebot", "Beratungstermine anbieten"
        SPERRE = "sperre", "Sperrzeit / Blocker"

    fahrlehrer = models.ForeignKey(
        Fahrlehrer, on_delete=models.CASCADE, related_name="regeln", verbose_name="Fahrlehrer"
    )
    regel_art = models.CharField(
        "Art der Regel",
        max_length=10,
        choices=RegelArt.choices,
        default=RegelArt.ANGEBOT,
    )
    terminart = models.ForeignKey(
        Terminart,
        on_delete=models.PROTECT,
        related_name="regeln",
        verbose_name="Terminart",
        null=True,
        blank=True,
    )
    sperrzeit_typ = models.CharField(
        "Sperrzeit-Typ",
        max_length=10,
        choices=SperrzeitTyp.choices,
        default=SperrzeitTyp.PRIVAT,
        blank=True,
        help_text="„Privat“ blockiert den Kalender, zählt aber in FSM nicht als Arbeitszeit.",
    )
    grund = models.CharField(
        "Grund / Notiz",
        max_length=200,
        blank=True,
        help_text="z. B. Kinder aus Kita abholen, Pause, Arzt",
    )
    bezeichnung = models.CharField(
        "Bezeichnung", max_length=120, blank=True, help_text="Nur zur Orientierung im Backend."
    )
    wochentage = models.JSONField(
        "Wochentage",
        default=list,
        help_text="Liste von Wochentagen (0 = Montag … 6 = Sonntag).",
    )
    beginn = models.TimeField("Von", default=dt.time(15, 30))
    ende = models.TimeField("Bis", default=dt.time(17, 0))
    intervall_wochen = models.PositiveIntegerField(
        "Rhythmus",
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(8)],
        help_text="1 = jede Woche, 2 = jede zweite Woche, usw.",
    )
    referenzwoche = models.DateField(
        "Referenzdatum",
        default=dt.date(2024, 1, 1),
        help_text="Nur bei mehrwöchigem Rhythmus relevant: Die Woche dieses Datums "
        "ist eine Woche, in der die Regel greift.",
    )
    gueltig_ab = models.DateField("Gültig ab", default=timezone.localdate)
    gueltig_bis = models.DateField(
        "Gültig bis", null=True, blank=True, help_text="Leer lassen für unbegrenzt."
    )
    feiertage_auslassen = models.BooleanField(
        "Feiertage auslassen",
        default=True,
        help_text="Überspringt gesetzliche Feiertage im Bundesland des Fahrlehrers.",
    )
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        app_label = "termine"
        verbose_name = "Rhythmus-Regel"
        verbose_name_plural = "Rhythmus-Regeln"
        ordering = ("fahrlehrer", "beginn")

    def __str__(self) -> str:
        if self.bezeichnung:
            return self.bezeichnung
        if self.regel_art == self.RegelArt.SPERRE:
            typ_lbl = self.get_sperrzeit_typ_display()
            return f"Blocker ({typ_lbl}): {self.wochentage_kurz} {self.beginn:%H:%M}–{self.ende:%H:%M}"
        return f"{self.wochentage_kurz} {self.beginn:%H:%M}–{self.ende:%H:%M}"

    def clean(self):
        fehler = {}
        if self.beginn >= self.ende:
            fehler["ende"] = "Das Ende muss nach dem Beginn liegen."
        if self.regel_art == self.RegelArt.ANGEBOT and not self.terminart_id:
            fehler["terminart"] = "Für Terminangebote ist eine Terminart erforderlich."
        if not isinstance(self.wochentage, list) or not self.wochentage:
            fehler["wochentage"] = "Mindestens ein Wochentag muss ausgewählt sein."
        else:
            ungueltig = [t for t in self.wochentage if t not in range(7)]
            if ungueltig:
                fehler["wochentage"] = f"Ungültige Wochentage: {ungueltig}"
        if self.gueltig_bis and self.gueltig_bis < self.gueltig_ab:
            fehler["gueltig_bis"] = "„Gültig bis“ muss nach „Gültig ab“ liegen."
        if fehler:
            raise ValidationError(fehler)

    @property
    def wochentage_kurz(self) -> str:
        return ", ".join(WOCHENTAG_KURZ[t] for t in sorted(self.wochentage) if t in WOCHENTAG_KURZ)

    def gilt_am(self, tag: dt.date) -> bool:
        """Greift diese Regel an diesem Kalendertag?

        Feiertage werden hier bewusst nicht geprüft – das macht der Generator,
        weil dafür das Bundesland des Fahrlehrers nötig ist.
        """
        if not self.aktiv:
            return False
        if tag < self.gueltig_ab:
            return False
        if self.gueltig_bis and tag > self.gueltig_bis:
            return False
        if tag.weekday() not in (self.wochentage or []):
            return False
        if self.intervall_wochen > 1:
            # Kalenderwochen-Abstand zwischen Referenzwoche und Zieltag.
            referenz_montag = self.referenzwoche - dt.timedelta(days=self.referenzwoche.weekday())
            ziel_montag = tag - dt.timedelta(days=tag.weekday())
            wochen_differenz = (ziel_montag - referenz_montag).days // 7
            if wochen_differenz % self.intervall_wochen != 0:
                return False
        return True


class Sperrzeit(models.Model):
    """Urlaub, Fahrstunde, Krankheit – blockiert einen Zeitraum komplett."""

    class Herkunft(models.TextChoices):
        MANUELL = "manuell", "Manuell"
        FSM = "fsm", "FSM-Import"

    Typ = SperrzeitTyp

    fahrlehrer = models.ForeignKey(
        Fahrlehrer, on_delete=models.CASCADE, related_name="sperrzeiten", verbose_name="Fahrlehrer"
    )
    regel = models.ForeignKey(
        RhythmusRegel,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="generierte_sperrzeiten",
        verbose_name="Erzeugt aus Regel",
    )
    beginn = models.DateTimeField("Beginn")
    ende = models.DateTimeField("Ende")
    grund = models.CharField("Grund", max_length=200, blank=True)
    typ = models.CharField(
        "Art der Sperrzeit",
        max_length=20,
        choices=Typ.choices,
        default=Typ.SONSTIGE,
    )
    fsm_id = models.TextField(
        "FSM-ID",
        blank=True,
        help_text="Optionale FSM-Termin-ID(s), wenn die Sperrzeit mit dem Fahrschulmanager verknüpft ist.",
    )
    herkunft = models.CharField(
        "Herkunft",
        max_length=20,
        choices=Herkunft.choices,
        default=Herkunft.MANUELL,
    )

    class Meta:
        app_label = "termine"
        verbose_name = "Sperrzeit"
        verbose_name_plural = "Sperrzeiten"
        ordering = ("-beginn",)
        indexes = [models.Index(fields=["fahrlehrer", "beginn", "ende"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ende__gt=models.F("beginn")),
                name="sperrzeit_ende_nach_beginn",
            )
        ]

    def __str__(self) -> str:
        return f"{self.fahrlehrer}: {timezone.localtime(self.beginn):%d.%m.%Y %H:%M} – {timezone.localtime(self.ende):%d.%m.%Y %H:%M}"

    def clean(self):
        if self.beginn and self.ende and self.beginn >= self.ende:
            raise ValidationError({"ende": "Das Ende muss nach dem Beginn liegen."})
