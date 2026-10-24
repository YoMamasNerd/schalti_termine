"""Rhythmus-Regeln: Liste, Bearbeiten, Schnell-Toggle, Live-Vorschau, Löschen."""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import (
    RhythmusRegelForm,
)
from ..models import (
    RhythmusRegel,
    Sperrzeit,
    Termin,
)
from ..services.planung import (
    generiere_termine,
    sperrzeiten_entfernen,
    termine_entfernen,
    vorschau,
    vorschau_einer_regel,
)
from .common import _erlaubte_fahrlehrer, _sicheres_ziel, mitarbeiter


def _berechne_vorschau_kontext(regel: RhythmusRegel) -> dict:
    """Bereitet Vorschau-Daten und Tageszusammenfassung für eine Regel auf."""
    daten = vorschau_einer_regel(regel)
    nach_tag: dict[dt.date, int] = {}
    for eintrag in daten["eintraege"]:
        tag = timezone.localtime(eintrag[0]).date()
        nach_tag[tag] = nach_tag.get(tag, 0) + 1
    vorschau_tage = sorted(nach_tag.items())
    return {
        **daten,
        "vorschau_tage": vorschau_tage,
        "vorschau_anzahl_gesamt": len(daten["eintraege"]),
        "vorschau_anzahl_tage": len(vorschau_tage),
        "regel": regel,
    }


@mitarbeiter
def regelliste(request):
    erlaubt = _erlaubte_fahrlehrer(request.user)
    alle_fahrlehrer = list(erlaubt.order_by("reihenfolge", "name"))
    basis_qs = (
        RhythmusRegel.objects.filter(fahrlehrer__in=erlaubt)
        .select_related("fahrlehrer", "terminart")
        .order_by("fahrlehrer__reihenfolge", "fahrlehrer__name", "beginn")
    )

    gewaehlter_slug = request.GET.get("fahrlehrer", "").strip()
    gewaehlter_fl = None
    if gewaehlter_slug:
        gewaehlter_fl = erlaubt.filter(slug=gewaehlter_slug).first()
        if gewaehlter_fl:
            basis_qs = basis_qs.filter(fahrlehrer=gewaehlter_fl)
        else:
            gewaehlter_slug = ""

    anzahl_gesamt = basis_qs.count()
    anzahl_angebote = basis_qs.filter(regel_art=RhythmusRegel.RegelArt.ANGEBOT).count()
    anzahl_sperren = basis_qs.filter(regel_art=RhythmusRegel.RegelArt.SPERRE).count()

    gewaehlte_art = request.GET.get("art", "").strip().lower()
    regeln = basis_qs
    if gewaehlte_art == RhythmusRegel.RegelArt.ANGEBOT:
        regeln = regeln.filter(regel_art=RhythmusRegel.RegelArt.ANGEBOT)
    elif gewaehlte_art == RhythmusRegel.RegelArt.SPERRE:
        regeln = regeln.filter(regel_art=RhythmusRegel.RegelArt.SPERRE)
    else:
        gewaehlte_art = ""

    return render(
        request,
        "staff/regeln.html",
        {
            "regeln": regeln,
            "alle_fahrlehrer": alle_fahrlehrer,
            "gewaehlter_slug": gewaehlter_slug,
            "gewaehlter_fahrlehrer": gewaehlter_fl,
            "gewaehlte_art": gewaehlte_art,
            "anzahl_gesamt": anzahl_gesamt,
            "anzahl_angebote": anzahl_angebote,
            "anzahl_sperren": anzahl_sperren,
        },
    )


@mitarbeiter
def regel_bearbeiten(request, pk: int | None = None):
    erlaubt = _erlaubte_fahrlehrer(request.user)
    regel = get_object_or_404(RhythmusRegel, pk=pk, fahrlehrer__in=erlaubt) if pk else None
    alter_fahrlehrer = regel.fahrlehrer if regel else None
    alte_art = regel.regel_art if regel else None

    if request.method == "POST":
        form = RhythmusRegelForm(request.POST, instance=regel)
        form.fields["fahrlehrer"].queryset = erlaubt
        if form.is_valid():
            regel_inst = form.save(commit=False)
            # Wenn Fahrlehrer oder Regel-Art gewechselt oder Regel deaktiviert wurde:
            # Bisherige freie Termine & Sperren dieser Regel sauber abräumen
            if regel and regel.pk and (
                alter_fahrlehrer != regel_inst.fahrlehrer
                or alte_art != regel_inst.regel_art
                or not regel_inst.aktiv
            ):
                termine_entfernen(Termin.objects.filter(regel=regel, status=Termin.Status.FREI))
                sperrzeiten_entfernen(Sperrzeit.objects.filter(regel=regel))

            regel = form.save()
            messages.success(request, "Regel gespeichert.")
            if "speichern_und_generieren" in request.POST:
                if alter_fahrlehrer and alter_fahrlehrer != regel.fahrlehrer:
                    generiere_termine(alter_fahrlehrer)
                bericht = generiere_termine(regel.fahrlehrer)
                messages.success(request, f"Termine aktualisiert: {bericht.als_text()}.")
            return redirect("termine:regeln")
    else:
        initial = {}
        if regel is None:
            kopie_pk = request.GET.get("kopie_von")
            if kopie_pk and kopie_pk.isdigit():
                vorlage = RhythmusRegel.objects.filter(
                    pk=int(kopie_pk), fahrlehrer__in=erlaubt
                ).first()
                if vorlage:
                    initial = {
                        "fahrlehrer": vorlage.fahrlehrer_id,
                        "regel_art": vorlage.regel_art,
                        "terminart": vorlage.terminart_id,
                        "sperrzeit_typ": vorlage.sperrzeit_typ,
                        "grund": vorlage.grund,
                        "bezeichnung": (
                            f"{vorlage.bezeichnung} (Kopie)"
                            if vorlage.bezeichnung
                            else ""
                        ),
                        "wochentage": vorlage.wochentage,
                        "beginn": vorlage.beginn,
                        "ende": vorlage.ende,
                        "intervall_wochen": vorlage.intervall_wochen,
                        "referenzwoche": vorlage.referenzwoche,
                        "gueltig_ab": timezone.localdate(),
                        "gueltig_bis": vorlage.gueltig_bis,
                        "feiertage_auslassen": vorlage.feiertage_auslassen,
                        "aktiv": True,
                    }
            fl_slug = request.GET.get("fahrlehrer", "").strip()
            if fl_slug and "fahrlehrer" not in initial:
                fl_obj = erlaubt.filter(slug=fl_slug).first()
                if fl_obj:
                    initial["fahrlehrer"] = fl_obj.pk
            elif erlaubt.count() == 1 and "fahrlehrer" not in initial:
                initial["fahrlehrer"] = erlaubt.first().pk

        form = RhythmusRegelForm(instance=regel, initial=initial)
        form.fields["fahrlehrer"].queryset = erlaubt

    # Vorschau: welche Termine / Blocker würde diese Regelkonstellation erzeugen?
    vorschau_tage = []
    vorschau_kontext = {}
    if regel is not None and regel.pk:
        vorschau_kontext = _berechne_vorschau_kontext(regel)
        if regel.regel_art == RhythmusRegel.RegelArt.SPERRE:
            vorschau_tage = vorschau_kontext["vorschau_tage"]
        else:
            soll, _ = vorschau(regel.fahrlehrer)
            nach_tag: dict[dt.date, int] = {}
            for beginn, (_, _, quelle) in soll.items():
                if quelle.pk == regel.pk:
                    tag_d = timezone.localtime(beginn).date()
                    nach_tag[tag_d] = nach_tag.get(tag_d, 0) + 1
            vorschau_tage = sorted(nach_tag.items())
            vorschau_kontext["vorschau_tage"] = vorschau_tage
            vorschau_kontext["vorschau_anzahl_gesamt"] = sum(
                anz for _, anz in vorschau_tage
            )
            vorschau_kontext["vorschau_anzahl_tage"] = len(vorschau_tage)

    return render(
        request,
        "staff/regel_formular.html",
        {
            "form": form,
            "regel": regel,
            "vorschau_tage": vorschau_tage,
            **vorschau_kontext,
        },
    )


@mitarbeiter
def regel_vorschau(request):
    """HTMX-Endpunkt: Berechnet live eine Vorschau anhand der aktuellen Formulareingaben."""
    erlaubt = _erlaubte_fahrlehrer(request.user)
    daten = request.POST if request.method == "POST" else request.GET
    regel_pk = daten.get("regel_id")
    regel = None
    if regel_pk and str(regel_pk).isdigit():
        regel = RhythmusRegel.objects.filter(
            pk=int(regel_pk), fahrlehrer__in=erlaubt
        ).first()

    form = RhythmusRegelForm(daten, instance=regel)
    form.fields["fahrlehrer"].queryset = erlaubt
    if form.is_valid():
        temp_regel = form.save(commit=False)
        kontext = _berechne_vorschau_kontext(temp_regel)
        kontext["vorschau_bereit"] = True
    else:
        kontext = {
            "vorschau_bereit": False,
            "vorschau_fehler": form.errors,
            "vorschau_tage": [],
        }
    return render(request, "staff/_regel_vorschau.html", kontext)


@mitarbeiter
@require_POST
def regel_toggle_aktiv(request, pk: int):
    """Schaltet eine Rhythmus-Regel direkt in der Liste aktiv/inaktiv und aktualisiert den Plan."""
    erlaubt = _erlaubte_fahrlehrer(request.user)
    regel = get_object_or_404(RhythmusRegel, pk=pk, fahrlehrer__in=erlaubt)
    regel.aktiv = not regel.aktiv
    regel.save(update_fields=["aktiv"])

    if not regel.aktiv:
        geloescht_t, entfallen_t = termine_entfernen(
            Termin.objects.filter(regel=regel, status=Termin.Status.FREI)
        )
        geloescht_s = sperrzeiten_entfernen(Sperrzeit.objects.filter(regel=regel))
        entfernt_summe = geloescht_t + len(entfallen_t) + geloescht_s
        messages.info(
            request,
            f"Regel „{regel}“ pausiert ({entfernt_summe} freie Einträge aus dem Kalender genommen).",
        )
    else:
        bericht = generiere_termine(regel.fahrlehrer)
        messages.success(
            request,
            f"Regel „{regel}“ aktiviert ({bericht.als_text()}).",
        )
    return redirect(_sicheres_ziel(request, "termine:regeln"))


@mitarbeiter
@require_POST
def regel_loeschen(request, pk: int):
    regel = get_object_or_404(
        RhythmusRegel, pk=pk, fahrlehrer__in=_erlaubte_fahrlehrer(request.user)
    )
    fahrlehrer = regel.fahrlehrer
    # Freie Termine aus dieser Regel mit entfernen, gebuchte bleiben bestehen.
    termine_entfernen(Termin.objects.filter(regel=regel, status=Termin.Status.FREI))
    # Generierte Sperrzeiten aus dieser Regel inkl. FSM-Blocker löschen
    sperrzeiten_entfernen(Sperrzeit.objects.filter(regel=regel))

    regel.delete()
    messages.success(
        request,
        f"Regel gelöscht. Bereits gebuchte Termine von {fahrlehrer.name} bleiben bestehen.",
    )
    return redirect("termine:regeln")


# --- Terminarten -----------------------------------------------------------
#
# Bis hierher lagen die Terminarten im Django-Admin. Sie sind aus ihm
# herausgelöst, damit die Fahrschule sie ohne Entwicklerhilfe pflegen kann –
# die Zugriffsstufe bleibt aber dieselbe: Eine Terminart wirkt auf das
# öffentliche Angebot *aller* Fahrlehrer, nicht auf den eigenen Kalender.
# Deshalb `@inhaber` und nicht `@mitarbeiter`; `test_zugriff.py` hält das fest.
