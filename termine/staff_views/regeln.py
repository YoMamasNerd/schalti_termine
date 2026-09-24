"""Rhythmus-Regeln: Liste, Bearbeiten, Löschen."""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import (
    RhythmusRegelForm,
)
from ..models import (
    FahrschulEinstellungen,
    RhythmusRegel,
    Sperrzeit,
    Termin,
)
from ..services.feiertage import feiertage_im_zeitraum
from ..services.planung import (
    generiere_termine,
    termine_entfernen,
    vorschau,
)
from .common import _erlaubte_fahrlehrer, mitarbeiter


@mitarbeiter
def regelliste(request):
    erlaubt = _erlaubte_fahrlehrer(request.user)
    regeln = (
        RhythmusRegel.objects.filter(fahrlehrer__in=erlaubt)
        .select_related("fahrlehrer", "terminart")
        .order_by("fahrlehrer__name", "beginn")
    )
    return render(request, "staff/regeln.html", {"regeln": regeln})

@mitarbeiter
def regel_bearbeiten(request, pk: int | None = None):
    erlaubt = _erlaubte_fahrlehrer(request.user)
    regel = get_object_or_404(RhythmusRegel, pk=pk, fahrlehrer__in=erlaubt) if pk else None

    if request.method == "POST":
        form = RhythmusRegelForm(request.POST, instance=regel)
        form.fields["fahrlehrer"].queryset = erlaubt
        if form.is_valid():
            regel = form.save()
            messages.success(request, "Regel gespeichert.")
            if "speichern_und_generieren" in request.POST:
                bericht = generiere_termine(regel.fahrlehrer)
                messages.success(request, f"Termine aktualisiert: {bericht.als_text()}.")
            return redirect("termine:regeln")
    else:
        form = RhythmusRegelForm(instance=regel)
        form.fields["fahrlehrer"].queryset = erlaubt

    # Vorschau: welche Termine / Blocker würde diese Regelkonstellation erzeugen?
    vorschau_tage = []
    if regel is not None:
        if regel.regel_art == RhythmusRegel.RegelArt.SPERRE:
            wochen = FahrschulEinstellungen.get_solo().horizont_wochen or 4
            von = timezone.localdate()
            bis = von + dt.timedelta(days=wochen * 7 - 1)
            feiertage = feiertage_im_zeitraum(regel.fahrlehrer.bundesland, von, bis)
            tag = von
            while tag <= bis:
                if regel.gilt_am(tag):
                    if not (tag in feiertage and regel.feiertage_auslassen):
                        vorschau_tage.append((tag, 1))
                tag += dt.timedelta(days=1)
        else:
            soll, bericht = vorschau(regel.fahrlehrer)
            nach_tag: dict[dt.date, int] = {}
            for beginn, (_, _, quelle) in soll.items():
                if quelle.pk == regel.pk:
                    nach_tag[timezone.localtime(beginn).date()] = (
                        nach_tag.get(timezone.localtime(beginn).date(), 0) + 1
                    )
            vorschau_tage = sorted(nach_tag.items())

    return render(
        request,
        "staff/regel_formular.html",
        {"form": form, "regel": regel, "vorschau_tage": vorschau_tage},
    )

@mitarbeiter
@require_POST
def regel_loeschen(request, pk: int):
    regel = get_object_or_404(
        RhythmusRegel, pk=pk, fahrlehrer__in=_erlaubte_fahrlehrer(request.user)
    )
    fahrlehrer = regel.fahrlehrer
    # Freie Termine aus dieser Regel mit entfernen, gebuchte bleiben bestehen.
    termine_entfernen(Termin.objects.filter(regel=regel, status=Termin.Status.FREI))
    # Generierte Sperrzeiten aus dieser Regel ebenfalls löschen
    sperren = list(Sperrzeit.objects.filter(regel=regel))
    fsm_ids = []
    for s in sperren:
        if s.fsm_id:
            fsm_ids.extend([fid.strip() for fid in s.fsm_id.split(",") if fid.strip()])
    if fsm_ids:
        from ..services import fsm_sync

        transaction.on_commit(lambda: fsm_sync.async_loesche_fsm_termine(fsm_ids))
    Sperrzeit.objects.filter(regel=regel).delete()

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
