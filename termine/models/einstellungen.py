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
    erinnerung_stunden_vorher = models.PositiveIntegerField(
        "Erinnerung vor Termin (Stunden)",
        default=24,
        blank=True,
        help_text="Wie viele Stunden vor dem Beratungstermin eine automatische Erinnerungs-E-Mail an den Kunden gesendet wird (z. B. 24 für einen Tag vorher, 0 = deaktiviert).",
    )
    reservierung_minuten = models.PositiveIntegerField(
        "Bestätigungsfrist (Minuten)",
        null=True,
        blank=True,
        validators=[MinValueValidator(5), MaxValueValidator(1440)],
        help_text=(
            "Wie lange Kunden Zeit haben, ihre Buchung per E-Mail-Link zu bestätigen "
            "(5 bis 1440). Leer = der Wert aus RESERVATION_MINUTES (Standard 30)."
        ),
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

    # --- SMTP- und E-Mail-Einstellungen ---
    email_host = models.CharField(
        "SMTP-Server (Host)",
        max_length=255,
        blank=True,
        help_text="z. B. smtp.strato.de, mail.meine-fahrschule.de oder smtp.gmail.com.",
    )
    email_port = models.PositiveIntegerField(
        "SMTP-Port",
        default=587,
        help_text="Standard: 587 (STARTTLS) oder 465 (SSL/TLS).",
    )
    email_user = models.CharField(
        "SMTP-Benutzername",
        max_length=255,
        blank=True,
        help_text="Meist Ihre vollständige E-Mail-Adresse für das Postfach.",
    )
    email_password = models.CharField(
        "SMTP-Passwort",
        max_length=255,
        blank=True,
        help_text="Das Passwort Ihres E-Mail-Postfachs / App-Passwort.",
    )
    email_use_tls = models.BooleanField(
        "STARTTLS aktivieren",
        default=True,
        help_text="Empfohlen für Port 587.",
    )
    email_use_ssl = models.BooleanField(
        "SSL/TLS aktivieren",
        default=False,
        help_text="Empfohlen für Port 465 (nicht zusammen mit STARTTLS verwenden).",
    )
    email_from = models.CharField(
        "Standard-Absenderadresse",
        max_length=255,
        blank=True,
        help_text="z. B. Fahrschule Schaltwerk <termine@fahrschule-schaltwerk.de> oder info@meine-fahrschule.de",
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

    @property
    def reservierungsdauer_minuten(self) -> int:
        """Effektive Bestätigungsfrist: DB-Wert oder Fallback auf die Settings."""
        if self.reservierung_minuten:
            return self.reservierung_minuten
        from django.conf import settings

        return settings.RESERVATION_MINUTES

    def get_effective_email_config(self) -> dict:
        """Liefert die E-Mail-/SMTP-Konfiguration aus der Datenbank."""
        if self.email_host:
            return {
                "host": self.email_host,
                "port": self.email_port or 587,
                "user": self.email_user or "",
                "password": self.email_password or "",
                "use_tls": self.email_use_tls,
                "use_ssl": self.email_use_ssl,
                "from_email": self.email_from or self.email_user or "mail@fahrschule-schaltwerk.de",
                "backend": "django.core.mail.backends.smtp.EmailBackend",
                "quelle": "datenbank",
            }
        return {
            "host": "",
            "port": 587,
            "user": "",
            "password": "",
            "use_tls": True,
            "use_ssl": False,
            "from_email": self.email_from or "mail@fahrschule-schaltwerk.de",
            "backend": "django.core.mail.backends.console.EmailBackend",
            "quelle": "datenbank",
        }

