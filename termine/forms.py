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
            self.fields.pop("fuehrerscheinklasse")

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
        initial=dt.time(14, 0),
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
    grund = forms.CharField(label="Grund", max_length=200, required=False)

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
            "terminart",
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
        vorhanden = self.initial.get("wochentage") or []
        if vorhanden:
            self.initial["wochentage"] = [str(tag) for tag in vorhanden]

    def clean_wochentage(self):
        return sorted(int(tag) for tag in self.cleaned_data["wochentage"])


class TerminartForm(forms.ModelForm):
    """Terminarten pflegen – ohne den Umweg über den Django-Admin.

    Das URL-Kürzel steht bewusst nicht im Formular: Es steckt in den Links,
    die die Fahrschule auf ihrer Webseite verteilt (`?art=erstberatung`).
    Beim Anlegen entsteht es aus dem Namen, danach bleibt es liegen – eine
    Umbenennung soll keine fremden Links zerreißen.

    Die Farbe fehlt aus einem anderen Grund: Sie wird zurzeit nirgends
    angezeigt. Ein Bedienfeld, das nichts bewirkt, kostet nur Vertrauen.
    """

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

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        doppelt = Terminart.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if doppelt.exists():
            raise forms.ValidationError("Eine Terminart mit diesem Namen gibt es bereits.")
        return name


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
            "vorlauf_stunden",
            "horizont_wochen",
            "fsm_id",
            "fsm_sync_aktiv",
            "aktiv",
            "reihenfolge",
        ]
        widgets = {"beschreibung": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "horizont_wochen": "Wie weit im Voraus Kunden buchen können. Die "
            "automatische Planung reicht genauso weit – was dahinter liegt, "
            "wird nicht angeboten.",
            "fsm_id": "UUID des Fahrlehrers im Fahrschulmanager (z. B. aus der FSM-Adressleiste oder Lehrerliste).",
        }

    def __init__(self, *args, inhaber: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if not inhaber:
            for name in self.NUR_INHABER:
                self.fields.pop(name)
