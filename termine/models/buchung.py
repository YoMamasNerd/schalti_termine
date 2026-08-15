from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from .common import neuer_token
from .einstellungen import Fuehrerscheinklasse
from .termin import Termin


class Buchung(models.Model):
    """Die Anmeldung eines Interessenten auf einen Termin."""

    class Status(models.TextChoices):
        OFFEN = "offen", "Offen (E-Mail-Bestätigung ausstehend)"
        BESTAETIGT = "bestaetigt", "Bestätigt"
        STORNIERT = "storniert", "Storniert"
        VERFALLEN = "verfallen", "Verfallen (nicht bestätigt)"

    AKTIVE_STATUS = (Status.OFFEN, Status.BESTAETIGT)

    termin = models.ForeignKey(
        Termin, on_delete=models.PROTECT, related_name="buchungen", verbose_name="Termin"
    )
    referenz = models.UUIDField("Referenz", default=uuid.uuid4, unique=True, editable=False)
    token = models.CharField(
        "Zugriffs-Token", max_length=64, default=neuer_token, unique=True, editable=False
    )

    name = models.CharField("Name", max_length=120)
    email = models.EmailField("E-Mail")
    telefon = models.CharField("Telefon", max_length=40, blank=True)
    fuehrerscheinklasse = models.CharField(
        "Führerscheinklasse", max_length=32, blank=True
    )
    nachricht = models.TextField("Nachricht", blank=True)

    status = models.CharField(
        "Status", max_length=12, choices=Status.choices, default=Status.OFFEN, db_index=True
    )
    reserviert_bis = models.DateTimeField("Reserviert bis", null=True, blank=True)

    erstellt_am = models.DateTimeField(auto_now_add=True)
    bestaetigt_am = models.DateTimeField(null=True, blank=True)
    storniert_am = models.DateTimeField(null=True, blank=True)
    storniert_von = models.CharField(
        "Storniert von", max_length=20, blank=True, help_text="kunde oder fahrschule"
    )
    erinnerung_am = models.DateTimeField(null=True, blank=True)
    einwilligung_am = models.DateTimeField(
        "Datenschutz-Einwilligung", null=True, blank=True
    )
    anonymisiert_am = models.DateTimeField(null=True, blank=True)
    email_hash = models.CharField(
        "E-Mail-Hash", max_length=64, blank=True, db_index=True,
        help_text="Pseudonymisierter Hash für Historien-Matching auch nach Datenlöschung"
    )

    class Meta:
        app_label = "termine"
        verbose_name = "Buchung"
        verbose_name_plural = "Buchungen"
        ordering = ("-erstellt_am",)
        constraints = [
            # Ein Termin darf nur genau eine offene oder bestätigte Buchung haben.
            # Stornierte und verfallene Buchungen bleiben als Historie erhalten.
            models.UniqueConstraint(
                fields=["termin"],
                condition=models.Q(status__in=["offen", "bestaetigt"]),
                name="nur_eine_aktive_buchung_pro_termin",
            )
        ]
        indexes = [models.Index(fields=["status", "reserviert_bis"])]

    def __str__(self) -> str:
        return f"{self.name} – {self.termin}"

    def save(self, *args, **kwargs):
        if self.email and not self.email_hash and self.email != "Gelöscht":
            import hashlib
            self.email_hash = hashlib.sha256(self.email.lower().strip().encode("utf-8")).hexdigest()
        super().save(*args, **kwargs)

    @property
    def ist_aktiv(self) -> bool:
        return self.status in self.AKTIVE_STATUS

    @property
    def ist_abgelaufen(self) -> bool:
        return (
            self.status == self.Status.OFFEN
            and self.reserviert_bis is not None
            and self.reserviert_bis < timezone.now()
        )

    @property
    def bestaetigungs_url(self) -> str:
        return f"{settings.SITE_BASE_URL}{reverse('termine:bestaetigen', args=[self.token])}"

    @property
    def verwaltungs_url(self) -> str:
        return f"{settings.SITE_BASE_URL}{reverse('termine:buchung', args=[self.token])}"

    @property
    def fuehrerscheinklasse_anzeige(self) -> str:
        if not self.fuehrerscheinklasse:
            return ""
        klasse = Fuehrerscheinklasse.objects.filter(code=self.fuehrerscheinklasse).first()
        if klasse:
            return str(klasse)
        return self.fuehrerscheinklasse

    def get_fuehrerscheinklasse_display(self) -> str:
        return self.fuehrerscheinklasse_anzeige
