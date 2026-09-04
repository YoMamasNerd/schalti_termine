from __future__ import annotations

import datetime as dt

from django.db import models
from django.utils import timezone

from .fahrlehrer import Fahrlehrer
from .planung import RhythmusRegel, Sperrzeit
from .terminart import Terminart


class TerminQuerySet(models.QuerySet):
    def buchbar(self, jetzt: dt.datetime | None = None):
        """Freie Termine innerhalb des Buchungsfensters, die nicht gesperrt sind.

        Das Fenster hat zwei Ränder: vorne der Mindest-Vorlauf, der am
        einzelnen Fahrlehrer hängen kann, hinten der fahrschulweite
        Planungshorizont. Gruppiert wird trotzdem nach Fahrlehrer – zwei
        Fahrlehrer mit verschiedenem Vorlauf ergeben zwei Fenster.
        """
        from django.db.models import Exists, OuterRef, Q

        jetzt = jetzt or timezone.now()
        heute = timezone.localdate(jetzt)

        fenster: dict[tuple[dt.datetime, dt.datetime], list[int]] = {}
        for fahrlehrer in Fahrlehrer.objects.filter(aktiv=True):
            grenzen = (fahrlehrer.fruehester_start(jetzt), fahrlehrer.spaetester_start(heute))
            fenster.setdefault(grenzen, []).append(fahrlehrer.pk)
        if not fenster:
            return self.none()

        if len(fenster) == 1:
            (frueh, spaet), _ = next(iter(fenster.items()))
            zeit_filter = Q(fahrlehrer__aktiv=True, beginn__gte=frueh, beginn__lte=spaet)
        else:
            zeit_filter = Q(pk__in=[])
            for (frueh, spaet), pks in fenster.items():
                zeit_filter |= Q(
                    fahrlehrer_id__in=pks, beginn__gte=frueh, beginn__lte=spaet
                )

        gesperrt = Sperrzeit.objects.filter(
            fahrlehrer=OuterRef("fahrlehrer"),
            beginn__lt=OuterRef("ende"),
            ende__gt=OuterRef("beginn"),
        )
        return (
            self.filter(status=Termin.Status.FREI)
            .filter(zeit_filter)
            .filter(terminart__aktiv=True)
            .annotate(ist_gesperrt=Exists(gesperrt))
            .filter(ist_gesperrt=False)
        )


class Termin(models.Model):
    """Ein konkreter, buchbarer Zeitslot."""

    class Status(models.TextChoices):
        FREI = "frei", "Frei"
        RESERVIERT = "reserviert", "Reserviert (wartet auf Bestätigung)"
        GEBUCHT = "gebucht", "Gebucht"
        ENTFALLEN = "entfallen", "Entfallen (nicht mehr im Angebot)"

    class Herkunft(models.TextChoices):
        MANUELL = "manuell", "Manuell angelegt"
        REGEL = "regel", "Aus Rhythmus-Regel"

    fahrlehrer = models.ForeignKey(
        Fahrlehrer, on_delete=models.CASCADE, related_name="termine", verbose_name="Fahrlehrer"
    )
    terminart = models.ForeignKey(
        Terminart, on_delete=models.PROTECT, related_name="termine", verbose_name="Terminart"
    )
    beginn = models.DateTimeField("Beginn", db_index=True)
    ende = models.DateTimeField("Ende")
    status = models.CharField(
        "Status", max_length=12, choices=Status.choices, default=Status.FREI, db_index=True
    )
    herkunft = models.CharField(
        "Herkunft", max_length=10, choices=Herkunft.choices, default=Herkunft.MANUELL
    )
    regel = models.ForeignKey(
        RhythmusRegel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="termine",
        verbose_name="Erzeugt durch Regel",
    )
    fsm_termin_id = models.CharField(
        "FSM-Termin-ID",
        max_length=64,
        blank=True,
        help_text="ID des synchronisierten Termins im Fahrschulmanager.",
    )
    notiz = models.CharField("Interne Notiz", max_length=200, blank=True)
    erstellt_am = models.DateTimeField(auto_now_add=True)
    geaendert_am = models.DateTimeField(auto_now=True)

    objects = TerminQuerySet.as_manager()

    class Meta:
        app_label = "termine"
        verbose_name = "Termin"
        verbose_name_plural = "Termine"
        ordering = ("beginn",)
        constraints = [
            models.UniqueConstraint(
                fields=["fahrlehrer", "beginn"], name="termin_eindeutig_pro_fahrlehrer"
            ),
            models.CheckConstraint(
                condition=models.Q(ende__gt=models.F("beginn")), name="termin_ende_nach_beginn"
            ),
        ]
        indexes = [
            models.Index(fields=["fahrlehrer", "beginn", "status"]),
            models.Index(fields=["status", "beginn"]),
        ]

    def __str__(self) -> str:
        lokal = timezone.localtime(self.beginn)
        return f"{self.fahrlehrer} – {lokal:%d.%m.%Y %H:%M}"

    @property
    def beginn_lokal(self) -> dt.datetime:
        return timezone.localtime(self.beginn)

    @property
    def ende_lokal(self) -> dt.datetime:
        return timezone.localtime(self.ende)

    @property
    def tag(self) -> dt.date:
        return self.beginn_lokal.date()

    @property
    def aktive_buchung(self):
        return self.buchungen.filter(
            status__in=["offen", "bestaetigt"]
        ).first()

    def ist_gesperrt(self) -> bool:
        return Sperrzeit.objects.filter(
            fahrlehrer=self.fahrlehrer, beginn__lt=self.ende, ende__gt=self.beginn
        ).exists()
