from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from ..services.feiertage import BUNDESLAENDER
from .common import FUEHRERSCHEINKLASSEN


class Fuehrerscheinklasse(models.Model):
    """Verfügbare Führerscheinklassen (FEK), die für Beratungstermine ausgewählt werden können."""

    code = models.CharField("Kürzel / Code", max_length=16, unique=True, help_text="z. B. B, B197, A, C1E")
    name = models.CharField("Bezeichnung", max_length=120, help_text="z. B. PKW, PKW Automatik-Regelung")
    aktiv = models.BooleanField("Verfügbar / Aktiv", default=True, help_text="Inaktive Klassen werden Kunden nicht angeboten.")
    reihenfolge = models.IntegerField("Reihenfolge", default=0)

    class Meta:
        app_label = "termine"
        verbose_name = "Führerscheinklasse"
        verbose_name_plural = "Führerscheinklassen"
        ordering = ("reihenfolge", "code")

    def __str__(self) -> str:
        if self.name and self.name != self.code:
            return f"{self.code} – {self.name}"
        return self.code

    @classmethod
    def choices_fuer_auswahl(cls) -> list[tuple[str, str]]:
        klassen = cls.objects.filter(aktiv=True)
        if not klassen.exists():
            return list(FUEHRERSCHEINKLASSEN)
        return [(k.code, str(k)) for k in klassen]


class FahrschulEinstellungen(models.Model):
    """Zentrale Einstellungen der Fahrschule (Mindest-Vorlauf, Planungshorizont, Bundesland)."""

    bundesland = models.CharField(
        "Standard-Bundesland",
        max_length=2,
        choices=BUNDESLAENDER,
        default="BE",
        help_text="Bestimmt die gesetzlichen Feiertage der Fahrschule (z. B. Berlin).",
    )
    vorlauf_stunden = models.PositiveIntegerField(
        "Mindest-Vorlauf (Stunden)",
        default=24,
        help_text="Termine, die früher als dieser Vorlauf beginnen, sind nicht mehr buchbar.",
    )
    horizont_wochen = models.PositiveIntegerField(
        "Planungshorizont (Wochen)",
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(52)],
        help_text="Wie viele Wochen im Voraus aus den Rhythmus-Regeln Termine erzeugt und angeboten werden.",
    )
    aktive_fuehrerscheinklassen = models.JSONField(
        "Verfügbare Führerscheinklassen",
        default=list,
        blank=True,
        help_text="Welche Führerscheinklassen die Fahrschule anbietet. Leer = alle Klassen stehen zur Auswahl.",
    )
    FSM_INTERVALL_CHOICES = (
        (15, "Alle 15 Minuten (Standard)"),
        (30, "Alle 30 Minuten"),
        (60, "Stündlich (60 Minuten)"),
        (120, "Alle 2 Stunden (120 Minuten)"),
        (0, "Deaktiviert (nur manueller Sync)"),
    )

    fsm_theorie_blockiert_beratung = models.BooleanField(
        "Theorieunterricht blockiert Beratungen",
        default=True,
        help_text="Wenn ein Fahrlehrer Theorieunterricht (FSM-Terminart PT / Theorie) hat, werden in dieser Zeit für alle Fahrlehrer keine Beratungen angeboten (gemeinsamer Raum).",
    )
    fsm_sync_intervall_minuten = models.PositiveIntegerField(
        "FSM Sync-Intervall",
        choices=FSM_INTERVALL_CHOICES,
        default=15,
        help_text="Wie oft die Belegungszeiten (Sperren) im Hintergrund automatisch mit dem Fahrschulmanager abgeglichen werden.",
    )

    class Meta:
        app_label = "termine"
        verbose_name = "Fahrschul-Einstellungen"
        verbose_name_plural = "Fahrschul-Einstellungen"

    def __str__(self) -> str:
        return f"Globale Einstellungen ({self.get_bundesland_display()}, {self.vorlauf_stunden}h Vorlauf, {self.horizont_wochen}w Horizont)"

    @classmethod
    def get_solo(cls) -> "FahrschulEinstellungen":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
