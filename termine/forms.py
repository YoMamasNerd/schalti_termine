"""Formulare für Buchung und Tagesplanung."""

from __future__ import annotations

import datetime as dt

from django import forms
from django.utils import timezone

from .models import (
    FUEHRERSCHEINKLASSEN,
    WOCHENTAGE,
    Fahrlehrer,
    RhythmusRegel,
    Sperrzeit,
    Terminart,
)


class BuchungsForm(forms.Form):
    """Das öffentliche Buchungsformular."""

    name = forms.CharField(
        label="Ihr Name", max_length=120, widget=forms.TextInput(attrs={"autocomplete": "name"})
    )
    email = forms.EmailField(
        label="E-Mail-Adresse",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
        help_text="An diese Adresse schicken wir den Bestätigungslink.",
    )
    telefon = forms.CharField(
        label="Telefon",
        max_length=40,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "tel"}),
        help_text="Freiwillig – hilft uns bei kurzfristigen Rückfragen.",
    )
    fuehrerscheinklasse = forms.ChoiceField(
        label="Gewünschte Führerscheinklasse",
        choices=[("", "Bitte wählen …")] + list(FUEHRERSCHEINKLASSEN),
        required=False,
    )
    nachricht = forms.CharField(
        label="Ihre Nachricht",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Worum geht es? Gibt es etwas, das wir vorher wissen sollten?",
    )
    datenschutz = forms.BooleanField(
        label="Ich bin damit einverstanden, dass meine Angaben zur Bearbeitung "
        "der Terminanfrage gespeichert werden.",
        required=True,
    )
    # Honeypot: Bots füllen dieses versteckte Feld aus, Menschen sehen es nicht.
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
        label="Bitte leer lassen",
    )

    def __init__(self, *args, terminart: Terminart | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if terminart is not None and not terminart.fuehrerscheinklasse_abfragen:
            self.fields.pop("fuehrerscheinklasse", None)
        elif "fuehrerscheinklasse" in self.fields:
            from .models import FahrschulEinstellungen, Fuehrerscheinklasse

            einst = FahrschulEinstellungen.get_solo()
            aktive_filter = set(einst.aktive_fuehrerscheinklassen or [])
            alle_aktiven = Fuehrerscheinklasse.choices_fuer_auswahl()
            if aktive_filter:
                auswahl = [c for c in alle_aktiven if c[0] in aktive_filter]
            else:
                auswahl = alle_aktiven
            self.fields["fuehrerscheinklasse"].choices = [("", "Bitte wählen …")] + auswahl

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Ungültige Eingabe.")
        return ""


class StornoForm(forms.Form):
    grund = forms.CharField(
        label="Grund (freiwillig)", required=False, widget=forms.Textarea(attrs={"rows": 3})
    )


class TagesplanungForm(forms.Form):
    """„An diesem Tag von X bis Y Beratungstermine anbieten.“"""

    fahrlehrer = forms.ModelChoiceField(
        label="Fahrlehrer", queryset=Fahrlehrer.objects.filter(aktiv=True)
    )
    terminart = forms.ModelChoiceField(
        label="Terminart",
        queryset=Terminart.objects.filter(aktiv=True),
        empty_label=None,  # die erste Terminart ist direkt vorausgewählt
    )
    tag = forms.DateField(
        label="Tag", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    von = forms.TimeField(
        label="Von",
        initial=dt.time(15, 30),
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
    )
    bis = forms.TimeField(
        label="Bis",
        initial=dt.time(17, 0),
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
    )
    notiz = forms.CharField(label="Interne Notiz", max_length=200, required=False)

    def clean(self):
        daten = super().clean()
        von, bis = daten.get("von"), daten.get("bis")
        if von and bis and von >= bis:
            self.add_error("bis", "Das Ende muss nach dem Beginn liegen.")
        tag = daten.get("tag")
        if tag and tag < timezone.localdate():
            self.add_error("tag", "Dieser Tag liegt in der Vergangenheit.")
        return daten


class SperrzeitForm(forms.Form):
    """Urlaub oder Abwesenheit über einen ganzen Zeitraum eintragen."""

    fahrlehrer = forms.ModelChoiceField(
        label="Fahrlehrer", queryset=Fahrlehrer.objects.filter(aktiv=True)
    )
    von_tag = forms.DateField(
        label="Von (Tag)", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    bis_tag = forms.DateField(
        label="Bis (Tag)", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    typ = forms.ChoiceField(
        label="Art der Sperrzeit",
        choices=Sperrzeit.Typ.choices,
        initial=Sperrzeit.Typ.SONSTIGE,
        required=False,
        help_text="„Privat“ blockiert den Kalender ebenfalls, zählt aber in FSM nicht als Arbeitszeit.",
    )
    grund = forms.CharField(label="Grund (z. B. Urlaub, Kita, Arzt)", max_length=200, required=False)

    def clean(self):
        daten = super().clean()
        von, bis = daten.get("von_tag"), daten.get("bis_tag")
        if von and bis and bis < von:
            self.add_error("bis_tag", "Das Ende muss nach dem Beginn liegen.")
        return daten


class WochentagWidget(forms.CheckboxSelectMultiple):
    pass


class RhythmusRegelForm(forms.ModelForm):
    """Regel-Formular mit Checkbox-Auswahl statt rohem JSON-Feld."""

    wochentage = forms.MultipleChoiceField(
        label="Wochentage", choices=WOCHENTAGE, widget=WochentagWidget, required=True
    )

    class Meta:
        model = RhythmusRegel
        fields = [
            "fahrlehrer",
            "regel_art",
            "terminart",
            "sperrzeit_typ",
            "grund",
            "bezeichnung",
            "wochentage",
            "beginn",
            "ende",
            "intervall_wochen",
            "referenzwoche",
            "gueltig_ab",
            "gueltig_bis",
            "feiertage_auslassen",
            "aktiv",
        ]
        widgets = {
            "beginn": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "ende": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "referenzwoche": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "gueltig_ab": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "gueltig_bis": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "terminart" in self.fields:
            self.fields["terminart"].required = False
        if "sperrzeit_typ" in self.fields:
            self.fields["sperrzeit_typ"].required = False
        if "regel_art" in self.fields:
            self.fields["regel_art"].required = False
        if not self.instance.pk:
            self.initial.setdefault("beginn", dt.time(15, 30))
            self.initial.setdefault("ende", dt.time(17, 0))
            self.initial.setdefault("regel_art", RhythmusRegel.RegelArt.ANGEBOT)
        vorhanden = self.initial.get("wochentage") or []
        if vorhanden:
            self.initial["wochentage"] = [str(tag) for tag in vorhanden]

    def clean_wochentage(self):
        return sorted(int(tag) for tag in self.cleaned_data["wochentage"])

    def clean(self):
        daten = super().clean()
        art = daten.get("regel_art") or RhythmusRegel.RegelArt.ANGEBOT
        daten["regel_art"] = art
        if art == RhythmusRegel.RegelArt.ANGEBOT and not daten.get("terminart"):
            self.add_error("terminart", "Für Terminangebote ist eine Terminart erforderlich.")
        return daten


class TerminartForm(forms.ModelForm):
    """Terminarten pflegen – ohne den Umweg über den Django-Admin."""

    class Meta:
        model = Terminart
        fields = [
            "name",
            "dauer_minuten",
            "puffer_minuten",
            "beschreibung",
            "ort",
            "fuehrerscheinklasse_abfragen",
            "aktiv",
            "reihenfolge",
        ]
        widgets = {"beschreibung": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.initial.setdefault("dauer_minuten", 90)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        doppelt = Terminart.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if doppelt.exists():
            raise forms.ValidationError("Eine Terminart mit diesem Namen gibt es bereits.")
        return name


class GlobaleEinstellungenForm(forms.ModelForm):
    """Fahrschulweite Einstellungen für Buchungsfenster, Planungshorizont, Bundesland und Klassen."""

    aktive_fuehrerscheinklassen = forms.MultipleChoiceField(
        label="Verfügbare Führerscheinklassen",
        choices=FUEHRERSCHEINKLASSEN,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "klassen-auswahl"}),
        required=False,
        help_text="Ausgewählte Klassen stehen Kunden im Buchungsformular zur Auswahl. Ist keine Klasse ausgewählt, sind alle Klassen wählbar.",
    )

    class Meta:
        from .models import FahrschulEinstellungen

        model = FahrschulEinstellungen
        fields = [
            "bundesland",
            "vorlauf_stunden",
            "horizont_wochen",
            "erinnerung_stunden_vorher",
            "reservierung_minuten",
            "aktive_fuehrerscheinklassen",
            "fsm_theorie_blockiert_beratung",
        ]
        help_texts = {
            "bundesland": "Bestimmt die gesetzlichen Feiertage der Fahrschule (z. B. Berlin).",
            "vorlauf_stunden": "Termine, die früher als dieser Vorlauf beginnen, sind nicht mehr buchbar.",
            "horizont_wochen": "Wie weit im Voraus Kunden buchen können und der Generator Termine anlegt.",
            "erinnerung_stunden_vorher": "Wie viele Stunden vor dem Beratungstermin Kunden automatisch per E-Mail erinnert werden (z. B. 24, 0 = deaktiviert).",
            "reservierung_minuten": "Wie lange Kunden Zeit haben, ihre Buchung per E-Mail-Link zu bestätigen (5 bis 1440, leer = 30 Minuten Standard).",
            "fsm_theorie_blockiert_beratung": "Verhindert Beratungstermine für alle Fahrlehrer während laufendem Theorieunterricht (gemeinsamer Raum).",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dieselbe Quelle wie im Buchungsformular. Stünde hier die fest
        # verdrahtete Liste, wäre eine unter /intern/klassen/ angelegte eigene
        # Klasse nicht ankreuzbar – und fiele, sobald überhaupt gefiltert wird,
        # aus dem Buchungsformular heraus, obwohl sie dort angeboten wird.
        from .models import Fuehrerscheinklasse

        self.fields["aktive_fuehrerscheinklassen"].choices = (
            Fuehrerscheinklasse.choices_fuer_auswahl()
        )
        if "bundesland" in self.fields:
            self.fields["bundesland"].required = False
        if "erinnerung_stunden_vorher" in self.fields:
            self.fields["erinnerung_stunden_vorher"].required = False
        if self.instance and self.instance.pk:
            vorhanden = self.instance.aktive_fuehrerscheinklassen or []
            self.initial.setdefault("aktive_fuehrerscheinklassen", vorhanden)

    def clean_bundesland(self):
        val = self.cleaned_data.get("bundesland")
        if not val and self.instance and self.instance.pk:
            return self.instance.bundesland or "BE"
        return val or "BE"

    def clean_erinnerung_stunden_vorher(self):
        val = self.cleaned_data.get("erinnerung_stunden_vorher")
        if val is None:
            if self.instance and self.instance.pk:
                return self.instance.erinnerung_stunden_vorher
            return 24
        return val

    def clean_aktive_fuehrerscheinklassen(self):
        return self.cleaned_data.get("aktive_fuehrerscheinklassen") or []


class FahrlehrerEinstellungenForm(forms.ModelForm):
    """Die Stammdaten eines Fahrlehrers, soweit er sie selbst pflegen darf.

    `aktiv` und `reihenfolge` wirken auf die öffentliche Seite aller
    Fahrlehrer, nicht nur auf die eigene – sie sieht deshalb nur der Inhaber.
    Der Login-Benutzer und das URL-Kürzel bleiben ganz im Django-Admin: Das
    eine ist Benutzerverwaltung, das andere trägt verteilte Links.
    """

    NUR_INHABER = ("aktiv", "reihenfolge")

    class Meta:
        model = Fahrlehrer
        fields = [
            "name",
            "email",
            "telefon",
            "beschreibung",
            "bundesland",
            "aktiv",
            "reihenfolge",
        ]
        widgets = {"beschreibung": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, inhaber: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if not inhaber:
            for name in self.NUR_INHABER:
                self.fields.pop(name)


class FuehrerscheinklasseForm(forms.ModelForm):
    """Formular zum Anlegen und Bearbeiten von Führerscheinklassen."""

    class Meta:
        from .models import Fuehrerscheinklasse

        model = Fuehrerscheinklasse
        fields = ["code", "name", "aktiv", "reihenfolge"]
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "z. B. B197"}),
            "name": forms.TextInput(attrs={"placeholder": "z. B. PKW (Automatik-Regelung)"}),
        }

    def clean_code(self):
        from .models import Fuehrerscheinklasse

        code = self.cleaned_data["code"].strip().upper()
        if not code:
            raise forms.ValidationError("Bitte ein Kürzel angeben.")
        doppelt = Fuehrerscheinklasse.objects.filter(code__iexact=code).exclude(
            pk=self.instance.pk
        )
        if doppelt.exists():
            raise forms.ValidationError("Eine Klasse mit diesem Kürzel existiert bereits.")
        return code


class SmtpEinstellungenForm(forms.ModelForm):
    """Formular zur Pflege der zentralen SMTP- und E-Mail-Einstellungen."""

    class Meta:
        from .models import FahrschulEinstellungen

        model = FahrschulEinstellungen
        fields = [
            "email_host",
            "email_port",
            "email_user",
            "email_password",
            "email_use_tls",
            "email_use_ssl",
            "email_from",
        ]
        widgets = {
            "email_host": forms.TextInput(attrs={"placeholder": "z. B. smtp.strato.de oder mail.meine-fahrschule.de"}),
            "email_port": forms.NumberInput(attrs={"placeholder": "587"}),
            "email_user": forms.TextInput(attrs={"placeholder": "z. B. info@meine-fahrschule.de"}),
            "email_password": forms.PasswordInput(render_value=True, attrs={"placeholder": "Passwort des E-Mail-Postfachs", "autocomplete": "off"}),
            "email_from": forms.TextInput(attrs={"placeholder": "z. B. Fahrschule Schaltwerk <termine@fahrschule-schaltwerk.de>"}),
        }
        help_texts = {
            "email_host": "Der SMTP-Server Ihres Mailanbieters (z. B. smtp.strato.de).",
            "email_port": "Standard: 587 (STARTTLS) oder 465 (SSL/TLS).",
            "email_user": "Benutzername oder E-Mail-Adresse für die Authentifizierung.",
            "email_password": "Das Passwort für Ihr Postfach oder ein anwendungsspezifisches App-Passwort.",
            "email_use_tls": "Verschlüsselung per STARTTLS (Standard für Port 587).",
            "email_use_ssl": "Verschlüsselung per SSL/TLS (Standard für Port 465).",
            "email_from": "Angezeigter Absender für Benachrichtigungen (z. B. Buchungsbestätigungen).",
        }

    def clean(self):
        daten = super().clean()
        tls = daten.get("email_use_tls")
        ssl = daten.get("email_use_ssl")
        if tls and ssl:
            self.add_error("email_use_ssl", "Bitte aktivieren Sie entweder STARTTLS (Port 587) oder SSL/TLS (Port 465), nicht beides gleichzeitig.")
        return daten


class BenutzerProfilForm(forms.ModelForm):
    """Persönliche Profildaten des angemeldeten Benutzers."""

    class Meta:
        from django.contrib.auth.models import User
        model = User
        fields = ["first_name", "last_name", "email"]
        labels = {
            "first_name": "Vorname",
            "last_name": "Nachname",
            "email": "E-Mail-Adresse",
        }
        widgets = {
            "first_name": forms.TextInput(attrs={"autocomplete": "given-name", "placeholder": "Vorname"}),
            "last_name": forms.TextInput(attrs={"autocomplete": "family-name", "placeholder": "Nachname"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "name@beispiel.de"}),
        }


