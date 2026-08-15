from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify


class Terminart(models.Model):
    """Was gebucht werden kann, z. B. „Erstberatung Führerschein Klasse B“."""

    name = models.CharField("Bezeichnung", max_length=120)
    slug = models.SlugField("URL-Kürzel", max_length=140, unique=True, blank=True)
    dauer_minuten = models.PositiveIntegerField(
        "Dauer (Minuten)",
        default=90,
        validators=[MinValueValidator(5), MaxValueValidator(600)],
    )
    puffer_minuten = models.PositiveIntegerField(
        "Puffer danach (Minuten)",
        default=0,
        help_text="Freie Zeit nach dem Termin, bevor der nächste Slot beginnt.",
    )
    beschreibung = models.TextField("Beschreibung", blank=True)
    ort = models.CharField(
        "Ort",
        max_length=200,
        blank=True,
        help_text="Erscheint in der Bestätigungsmail und im Kalendereintrag.",
    )
    farbe = models.CharField(
        "Farbe",
        max_length=7,
        default="#2f6f4e",
        help_text="Hex-Farbe für die Darstellung im Backend, z. B. #2f6f4e",
    )
    fuehrerscheinklasse_abfragen = models.BooleanField(
        "Führerscheinklasse abfragen",
        default=True,
        help_text="Blendet im Buchungsformular die Auswahl der Führerscheinklasse ein.",
    )
    aktiv = models.BooleanField("Aktiv", default=True)
    reihenfolge = models.IntegerField("Reihenfolge", default=0)

    class Meta:
        app_label = "termine"
        verbose_name = "Terminart"
        verbose_name_plural = "Terminarten"
        ordering = ("reihenfolge", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.dauer_minuten} Min.)"

    def save(self, *args, **kwargs):
        if not self.slug:
            basis = slugify(self.name) or "terminart"
            slug = basis
            n = 2
            while Terminart.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{basis}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def schrittweite_minuten(self) -> int:
        return self.dauer_minuten + self.puffer_minuten
