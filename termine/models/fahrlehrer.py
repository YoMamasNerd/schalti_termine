from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from ..services.feiertage import BUNDESLAENDER
from .common import neuer_token
from .einstellungen import FahrschulEinstellungen


class Fahrlehrer(models.Model):
    """Eine Person, die Beratungstermine anbietet."""

    benutzer = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fahrlehrer",
        verbose_name="Benutzerkonto",
        help_text="Optional: Verbindet diesen Fahrlehrer mit einem Login-Konto.",
    )
    name = models.CharField("Name", max_length=120)
    slug = models.SlugField(
        "URL-Kürzel",
        max_length=140,
        unique=True,
        blank=True,
        help_text="Wird für die persönliche Buchungsseite verwendet.",
    )
    email = models.EmailField(
        "E-Mail",
        help_text="Hierhin gehen die Benachrichtigungen über neue Buchungen.",
    )
    telefon = models.CharField("Telefon", max_length=40, blank=True)
    beschreibung = models.TextField(
        "Beschreibung",
        blank=True,
        help_text="Wird auf der öffentlichen Buchungsseite angezeigt.",
    )
    bundesland = models.CharField(
        "Bundesland",
        max_length=2,
        choices=BUNDESLAENDER,
        default="BE",
        help_text="Bestimmt, welche gesetzlichen Feiertage bei der automatischen "
        "Terminplanung übersprungen werden.",
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
        help_text="Wie viele Wochen im Voraus aus den Rhythmus-Regeln Termine "
        "erzeugt und zur Buchung angeboten werden.",
    )
    feed_token = models.CharField(
        "Feed-Token",
        max_length=64,
        default=neuer_token,
        unique=True,
        help_text="Geheimer Teil der Kalender-Abo-URL. Beim Zurücksetzen wird das "
        "alte Abo ungültig.",
    )
    fsm_id = models.CharField(
        "FSM-Fahrlehrer-ID",
        max_length=64,
        blank=True,
        help_text="Optionale ID des Fahrlehrers im Fahrschulmanager für automatische Synchronisation.",
    )
    fsm_sync_aktiv = models.BooleanField(
        "FSM-Sync aktiv",
        default=True,
        help_text="Termine und Belegungszeiten automatisch mit dem Fahrschulmanager synchronisieren.",
    )
    aktiv = models.BooleanField("Aktiv", default=True)
    reihenfolge = models.IntegerField("Reihenfolge", default=0)

    class Meta:
        app_label = "termine"
        verbose_name = "Fahrlehrer"
        verbose_name_plural = "Fahrlehrer"
        ordering = ("reihenfolge", "name")

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            basis = slugify(self.name) or "fahrlehrer"
            slug = basis
            n = 2
            while Fahrlehrer.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{basis}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("termine:fahrlehrer", args=[self.slug])

    @property
    def feed_url(self) -> str:
        pfad = reverse("termine:ics_feed", args=[self.feed_token])
        return f"{settings.SITE_BASE_URL}{pfad}"

    def fruehester_start(self, jetzt: dt.datetime | None = None) -> dt.datetime:
        """Ab wann ein Termin frühestens buchbar ist (Mindest-Vorlauf)."""
        jetzt = jetzt or timezone.now()
        # Der Fahrlehrer kann nur einen längeren Vorlauf fordern als global
        # eingestellt ist – nie einen kürzeren, damit das fahrschulweite
        # Minimum gilt, egal wer am eigenen Kalender dreht.
        vorlauf = max(self.vorlauf_stunden, FahrschulEinstellungen.get_solo().vorlauf_stunden)
        return jetzt + dt.timedelta(hours=vorlauf)

    def spaetester_start(self, heute: dt.date | None = None) -> dt.datetime:
        """Bis wann ein Termin höchstens buchbar ist (Planungshorizont)."""
        heute = heute or timezone.localdate()
        wochen = FahrschulEinstellungen.get_solo().horizont_wochen or settings.DEFAULT_HORIZON_WEEKS
        letzter_tag = heute + dt.timedelta(days=wochen * 7 - 1)
        return timezone.make_aware(dt.datetime.combine(letzter_tag, dt.time.max))
