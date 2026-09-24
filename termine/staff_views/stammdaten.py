"""Stammdaten der Fahrschule: Terminarten, Klassen, neuer Fahrlehrer."""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from ..forms import (
    FahrlehrerEinstellungenForm,
    FuehrerscheinklasseForm,
    TerminartForm,
)
from ..models import (
    FUEHRERSCHEINKLASSEN,
    Fuehrerscheinklasse,
    Terminart,
)
from .common import inhaber


@inhaber
def terminartenliste(request):
    terminarten = Terminart.objects.annotate(
        anzahl_termine=Count("termine", distinct=True),
        anzahl_regeln=Count("regeln", distinct=True),
    ).order_by("reihenfolge", "name")
    return render(request, "staff/terminarten.html", {"terminarten": terminarten})

@inhaber
def terminart_bearbeiten(request, pk: int | None = None):
    terminart = get_object_or_404(Terminart, pk=pk) if pk else None

    if request.method == "POST":
        form = TerminartForm(request.POST, instance=terminart)
        if form.is_valid():
            terminart = form.save()
            messages.success(request, f"Terminart „{terminart.name}“ gespeichert.")
            return redirect("termine:terminarten")
    else:
        form = TerminartForm(instance=terminart)

    # Womit die Terminart verbunden ist – das entscheidet, ob sie sich noch
    # löschen lässt oder nur noch abschalten.
    verwendung = {}
    if terminart is not None:
        verwendung = {
            "termine": terminart.termine.count(),
            "regeln": terminart.regeln.count(),
        }

    return render(
        request,
        "staff/terminart_formular.html",
        {"form": form, "terminart": terminart, "verwendung": verwendung},
    )

@inhaber
@require_POST
def terminart_loeschen(request, pk: int):
    """Löscht eine Terminart – aber nur eine, die noch nirgends hängt.

    `Termin.terminart` und `RhythmusRegel.terminart` stehen auf PROTECT: Ein
    Löschversuch endete sonst im ProtectedError, und die Historie einer
    Buchung hinge an einem Termin ohne Art. Wer eine benutzte Terminart
    loswerden will, nimmt ihr den Haken bei „Aktiv“ – dann verschwindet sie
    aus Formularen und öffentlicher Auswahl, ohne die Vergangenheit zu
    verbiegen.
    """
    terminart = get_object_or_404(Terminart, pk=pk)
    termine = terminart.termine.count()
    regeln = terminart.regeln.count()

    if termine or regeln:
        messages.error(
            request,
            f"„{terminart.name}“ wird noch verwendet ({termine} Termine, "
            f"{regeln} Regeln) und kann deshalb nicht gelöscht werden. "
            "Nehmen Sie stattdessen den Haken bei „Aktiv“ heraus.",
        )
        return redirect("termine:terminart_bearbeiten", pk=terminart.pk)

    name = terminart.name
    terminart.delete()
    messages.success(request, f"Terminart „{name}“ gelöscht.")
    return redirect("termine:terminarten")


# --- Führerscheinklassen (FEK) ---------------------------------------------

@inhaber
def klassenliste(request):
    """Übersicht aller Führerscheinklassen mit Bearbeitungs- und Löschmöglichkeiten."""
    klassen = Fuehrerscheinklasse.objects.all().order_by("reihenfolge", "code")
    if not klassen.exists():
        for i, (code, full_name) in enumerate(FUEHRERSCHEINKLASSEN):
            name_teil = full_name.split("–", 1)[-1].strip() if "–" in full_name else full_name
            Fuehrerscheinklasse.objects.create(
                code=code,
                name=name_teil,
                aktiv=True,
                reihenfolge=i,
            )
        klassen = Fuehrerscheinklasse.objects.all().order_by("reihenfolge", "code")

    return render(request, "staff/klassen.html", {"klassen": klassen})

@inhaber
def klasse_bearbeiten(request, pk: int | None = None):
    klasse = get_object_or_404(Fuehrerscheinklasse, pk=pk) if pk else None

    if request.method == "POST":
        form = FuehrerscheinklasseForm(request.POST, instance=klasse)
        if form.is_valid():
            klasse = form.save()
            messages.success(request, f"Führerscheinklasse „{klasse.code}“ gespeichert.")
            return redirect("termine:klassen")
    else:
        form = FuehrerscheinklasseForm(instance=klasse)

    return render(
        request,
        "staff/klasse_formular.html",
        {"form": form, "klasse": klasse},
    )

@inhaber
@require_POST
def klasse_loeschen(request, pk: int):
    klasse = get_object_or_404(Fuehrerscheinklasse, pk=pk)
    code = klasse.code
    klasse.delete()
    messages.success(request, f"Führerscheinklasse „{code}“ gelöscht.")
    return redirect("termine:klassen")


# --- Einstellungen ---------------------------------------------------------

@inhaber
def fahrlehrer_neu(request):
    """Legt einen Fahrlehrer an, ohne den Umweg über den Django-Admin.

    Das Login bleibt Sache der Benutzerverwaltung: Wer sich anmelden können
    soll, bekommt im Admin ein Konto und wird dort mit diesem Eintrag
    verbunden. Ein Fahrlehrer ohne Login ist trotzdem sinnvoll – der Inhaber
    plant dann für ihn mit.
    """
    if request.method == "POST":
        form = FahrlehrerEinstellungenForm(request.POST, inhaber=True)
        if form.is_valid():
            fahrlehrer = form.save()
            messages.success(
                request,
                f"{fahrlehrer.name} angelegt. Für einen eigenen Zugang muss noch ein "
                "Login-Benutzer angelegt und in den Stammdaten verknüpft werden.",
            )
            return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}")
    else:
        form = FahrlehrerEinstellungenForm(inhaber=True)
    return render(request, "staff/fahrlehrer_formular.html", {"form": form})
