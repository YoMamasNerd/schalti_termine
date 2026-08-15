"""Datenmodell für Beratungstermine einer Fahrschule."""

from __future__ import annotations

import datetime as dt
import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .services.feiertage import BUNDESLAENDER

WOCHENTAGE: tuple[tuple[int, str], ...] = (
    (0, "Montag"),
    (1, "Dienstag"),
    (2, "Mittwoch"),
    (3, "Donnerstag"),
    (4, "Freitag"),
    (5, "Samstag"),
    (6, "Sonntag"),
)

WOCHENTAG_KURZ = {0: "Mo", 1: "Di", 2: "Mi", 3: "Do", 4: "Fr", 5: "Sa", 6: "So"}

FUEHRERSCHEINKLASSEN: tuple[tuple[str, str], ...] = (
    ("AM", "AM – Roller / Kleinkraftrad"),
    ("A1", "A1 – Leichtkraftrad"),
    ("A2", "A2 – Motorrad (mittel)"),
    ("A", "A – Motorrad (unbeschränkt)"),
    ("B", "B – PKW"),
    ("B197", "B197 – PKW (Automatik-Regelung)"),
    ("B96", "B96 – PKW mit Anhänger"),
    ("BE", "BE – PKW mit Anhänger"),
    ("C1", "C1 – LKW bis 7,5 t"),
    ("C", "C – LKW"),
    ("CE", "CE – LKW mit Anhänger"),
    ("D1", "D1 – Kleinbus"),
    ("D", "D – Bus"),
    ("L", "L – Land-/Forstwirtschaft"),
    ("T", "T – Zugmaschine"),
    ("MOFA", "Mofa-Prüfbescheinigung"),
    ("SONST", "Andere / weiß ich noch nicht"),
)


def neuer_token() -> str:
    """Kryptografisch sicherer Token für Links in E-Mails."""
    return secrets.token_urlsafe(32)


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

    class Meta:
        verbose_name = "Fahrschul-Einstellungen"
        verbose_name_plural = "Fahrschul-Einstellungen"

    def __str__(self) -> str:
        return f"Globale Einstellungen ({self.get_bundesland_display()}, {self.vorlauf_stunden}h Vorlauf, {self.horizont_wochen}w Horizont)"

    @classmethod
    def get_solo(cls) -> "FahrschulEinstellungen":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


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
        vorlauf = FahrschulEinstellungen.get_solo().vorlauf_stunden
        return jetzt + dt.timedelta(hours=vorlauf)

    def spaetester_start(self, heute: dt.date | None = None) -> dt.datetime:
        """Bis wann ein Termin höchstens buchbar ist (Planungshorizont)."""
        heute = heute or timezone.localdate()
        wochen = FahrschulEinstellungen.get_solo().horizont_wochen or settings.DEFAULT_HORIZON_WEEKS
        letzter_tag = heute + dt.timedelta(days=wochen * 7 - 1)
        return timezone.make_aware(dt.datetime.combine(letzter_tag, dt.time.max))


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


class RhythmusRegel(models.Model):
    """Wiederkehrende Verfügbarkeit, aus der Termine im Voraus erzeugt werden.

    Beispiel: „montags und mittwochs 14:00–18:00, jede Woche“ oder
    „freitags 09:00–12:00, jede 2. Woche“.
    """

    fahrlehrer = models.ForeignKey(
        Fahrlehrer, on_delete=models.CASCADE, related_name="regeln", verbose_name="Fahrlehrer"
    )
    terminart = models.ForeignKey(
        Terminart, on_delete=models.PROTECT, related_name="regeln", verbose_name="Terminart"
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
        verbose_name = "Rhythmus-Regel"
        verbose_name_plural = "Rhythmus-Regeln"
        ordering = ("fahrlehrer", "beginn")

    def __str__(self) -> str:
        if self.bezeichnung:
            return self.bezeichnung
        return f"{self.wochentage_kurz} {self.beginn:%H:%M}–{self.ende:%H:%M}"

    def clean(self):
        fehler = {}
        if self.beginn >= self.ende:
            fehler["ende"] = "Das Ende muss nach dem Beginn liegen."
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

    fahrlehrer = models.ForeignKey(
        Fahrlehrer, on_delete=models.CASCADE, related_name="sperrzeiten", verbose_name="Fahrlehrer"
    )
    beginn = models.DateTimeField("Beginn")
    ende = models.DateTimeField("Ende")
    grund = models.CharField("Grund", max_length=200, blank=True)
    fsm_id = models.CharField(
        "FSM-ID",
        max_length=64,
        blank=True,
        help_text="Optionale FSM-Termin-ID, wenn die Sperrzeit aus dem Fahrschulmanager stammt.",
    )

    class Meta:
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


class TerminQuerySet(models.QuerySet):
    def buchbar(self, jetzt: dt.datetime | None = None):
        """Freie Termine innerhalb des Buchungsfensters, die nicht gesperrt sind.

        Das Fenster hat zwei Ränder, und beide hängen am einzelnen Fahrlehrer:
        vorne der Mindest-Vorlauf, hinten der Planungshorizont. Sie in der
        Datenbank auszurechnen (`beginn - jetzt >= vorlauf_stunden`) wäre die
        elegantere Fassung, scheitert aber an SQLite: Dort ist „Ganzzahl mal
        Zeitspanne" kein gültiger Operator, und SQLite ist für
        Einzelplatz-Installationen ausdrücklich vorgesehen.

        Deshalb werden die Grenzen in Python gebildet. Haben alle Fahrlehrer
        dieselben Werte – der Normalfall –, ist es eine einzige Bedingung;
        erst bei unterschiedlichen Werten entsteht eine Kette.
        """
        from django.db.models import Exists, OuterRef, Q

        jetzt = jetzt or timezone.now()
        heute = timezone.localdate(jetzt)

        # Nach gleichem Fenster gruppieren, nicht nach Fahrlehrer: Zwei
        # Fahrlehrer mit denselben Einstellungen ergeben eine Bedingung.
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
        # Zurückgezogen, aber nicht gelöscht: Der Termin trägt eine
        # Buchungshistorie – etwa eine Stornierung –, und die ist der Beleg
        # dafür, dass hier einmal jemand gebucht hat. Ein Aufräumlauf darf so
        # einen Beleg nicht wegwerfen, also verschwindet nur das Angebot.
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
            status__in=[Buchung.Status.OFFEN, Buchung.Status.BESTAETIGT]
        ).first()

    def ist_gesperrt(self) -> bool:
        return Sperrzeit.objects.filter(
            fahrlehrer=self.fahrlehrer, beginn__lt=self.ende, ende__gt=self.beginn
        ).exists()



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
        "Führerscheinklasse", max_length=8, choices=FUEHRERSCHEINKLASSEN, blank=True
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
