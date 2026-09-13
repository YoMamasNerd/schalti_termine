from __future__ import annotations

from django.conf import settings
from django.db import models


class LogLevel(models.TextChoices):
    DEBUG = "DEBUG", "Debug"
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warnung"
    ERROR = "ERROR", "Fehler"
    CRITICAL = "CRITICAL", "Kritisch"


class LogKategorie(models.TextChoices):
    BUCHUNG = "buchung", "Buchungen"
    TERMIN = "termin", "Termine & Planung"
    FAHRLEHRER = "fahrlehrer", "Fahrlehrer"
    EINSTELLUNGEN = "einstellungen", "Einstellungen"
    AUTH = "auth", "Authentifizierung"
    SYSTEM = "system", "System / Worker"
    FSM = "fsm", "FSM-Gateway"


class SystemLog(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Erstellt am")
    level = models.CharField(
        max_length=20,
        choices=LogLevel.choices,
        default=LogLevel.INFO,
        db_index=True,
        verbose_name="Level",
    )
    kategorie = models.CharField(
        max_length=30,
        choices=LogKategorie.choices,
        default=LogKategorie.SYSTEM,
        db_index=True,
        verbose_name="Kategorie / Art",
    )
    aktion = models.CharField(
        max_length=100,
        db_index=True,
        verbose_name="Aktion",
        help_text="z. B. 'buchung_erstellt', 'termin_angelegt', 'buchung_storniert'",
    )
    titel = models.CharField(max_length=255, verbose_name="Titel")
    nachricht = models.TextField(blank=True, verbose_name="Nachricht / Details")
    benutzer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="termine_system_logs",
        verbose_name="Benutzer",
    )
    details = models.JSONField(default=dict, blank=True, verbose_name="Zusatzdaten (JSON)")
    ip_adresse = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP-Adresse")

    class Meta:
        verbose_name = "System-Log"
        verbose_name_plural = "System-Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["-created_at", "kategorie"]),
            models.Index(fields=["-created_at", "level"]),
            models.Index(fields=["aktion"]),
        ]

    def __str__(self) -> str:
        return f"[{self.created_at:%Y-%m-%d %H:%M:%S}] [{self.level}] [{self.kategorie}] {self.titel}"
