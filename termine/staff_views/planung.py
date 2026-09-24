"""Tagesplanung: Termine, Sperrzeiten, Kollisionen, Generierung."""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from ..forms import (
    SperrzeitForm,
    TagesplanungForm,
)
from ..models import (
    WOCHENTAG_KURZ,
    Buchung,
    Fahrlehrer,
    FahrschulEinstellungen,
    KollisionsIgnorier,
    Sperrzeit,
    Termin,
    Terminart,
)
from ..services.feiertage import feiertage_im_zeitraum
from ..services.planung import (
    generiere_termine,
    lokal,
    termine_entfernen,
    termine_manuell_anlegen,
)
from .common import _erlaubte_fahrlehrer, _gewaehlter_fahrlehrer, _sicheres_ziel, mitarbeiter
from .dashboard import _dashboard_ziel, _ist_fahrstunde_sperre


@mitarbeiter
def tagesplanung(request):
    """Wochenansicht mit der Möglichkeit, jeden Tag einzeln zu planen."""
    fahrlehrer, erlaubt = _gewaehlter_fahrlehrer(request)
    if fahrlehrer is None:
        messages.warning(request, "Bitte legen Sie zuerst einen Fahrlehrer an.")
        return redirect("termine:fahrlehrer_neu")

    heute = timezone.localdate()
    roh_woche = request.GET.get("woche")
    try:
        start_tag = dt.date.fromisoformat(roh_woche) if roh_woche else heute
    except ValueError:
        start_tag = heute
    end_tag = start_tag + dt.timedelta(days=6)

    termine = (
        Termin.objects.filter(
            fahrlehrer=fahrlehrer,
            beginn__gte=lokal(start_tag, dt.time.min),
            beginn__lte=lokal(end_tag, dt.time.max),
        )
        # Entfallene Termine stehen nur noch als Beleg in der Datenbank; in der
        # Wochenansicht wären sie eine Zeile, an der es nichts zu tun gibt.
        .exclude(status=Termin.Status.ENTFALLEN)
        .select_related("terminart")
        .prefetch_related("buchungen")
        .order_by("beginn")
    )

    nach_tag: dict[dt.date, list[Termin]] = {
        start_tag + dt.timedelta(days=i): [] for i in range(7)
    }
    for termin in termine:
        nach_tag.setdefault(termin.tag, []).append(termin)

    feiertage = feiertage_im_zeitraum(fahrlehrer.bundesland, start_tag, end_tag)
    sperren = Sperrzeit.objects.filter(
        fahrlehrer=fahrlehrer,
        beginn__lt=lokal(end_tag, dt.time.max),
        ende__gt=lokal(start_tag, dt.time.min),
    )

    tage = []
    for i in range(7):
        tag = start_tag + dt.timedelta(days=i)
        tag_start = lokal(tag, dt.time.min)
        tag_ende = lokal(tag, dt.time.max)
        tages_termine = nach_tag.get(tag, [])
        tages_sperren = [s for s in sperren if s.beginn < tag_ende and s.ende > tag_start]

        eintraege = []
        for t in tages_termine:
            buchung = t.aktive_buchung
            verfallene = next(
                (b for b in sorted(t.buchungen.all(), key=lambda x: x.erstellt_am, reverse=True) if b.status == Buchung.Status.VERFALLEN), None
            )
            eintraege.append(
                {
                    "art": "termin",
                    "zeit": t.beginn,
                    "beginn_uhrzeit": timezone.localtime(t.beginn).strftime("%H:%M"),
                    "ende_uhrzeit": timezone.localtime(t.ende).strftime("%H:%M"),
                    "status": t.status,
                    "titel": t.terminart.name,
                    "detail": buchung.name if buchung else "",
                    "termin": t,
                    "buchung": buchung,
                    "verfallene_buchung": verfallene,
                }
            )

        # FSM-Fahrstunden zu kompakten Blöcken zusammenfassen
        fahrstunden_sperren = [s for s in tages_sperren if _ist_fahrstunde_sperre(s)]
        sonstige_sperren = [s for s in tages_sperren if not _ist_fahrstunde_sperre(s)]

        fahrstunden_sperren.sort(key=lambda s: s.beginn)

        # Zusammenhängende Fahrstunden-Blöcke bilden (Pausen <= 20 Min verschmelzen)
        MAX_PAUSE_MINUTEN = 20
        bloecke = []
        aktueller_block = None

        for s in fahrstunden_sperren:
            s_beginn = timezone.localtime(s.beginn)
            s_ende = timezone.localtime(s.ende)
            grund_text = (s.grund or "").strip()
            sauberer_grund = grund_text[4:].strip() if grund_text.startswith("FSM:") else grund_text

            if aktueller_block is None:
                aktueller_block = {
                    "beginn": s_beginn,
                    "ende": s_ende,
                    "items": [{
                        "sperre": s,
                        "beginn": s_beginn,
                        "ende": s_ende,
                        "text": sauberer_grund or "Fahrstunde",
                    }],
                }
            else:
                pause_min = (s_beginn - aktueller_block["ende"]).total_seconds() / 60.0
                if s_beginn <= aktueller_block["ende"] or (0 <= pause_min <= MAX_PAUSE_MINUTEN):
                    if s_ende > aktueller_block["ende"]:
                        aktueller_block["ende"] = s_ende
                    aktueller_block["items"].append({
                        "sperre": s,
                        "beginn": s_beginn,
                        "ende": s_ende,
                        "text": sauberer_grund or "Fahrstunde",
                    })
                else:
                    bloecke.append(aktueller_block)
                    aktueller_block = {
                        "beginn": s_beginn,
                        "ende": s_ende,
                        "items": [{
                            "sperre": s,
                            "beginn": s_beginn,
                            "ende": s_ende,
                            "text": sauberer_grund or "Fahrstunde",
                        }],
                    }

        if aktueller_block:
            bloecke.append(aktueller_block)

        for b in bloecke:
            anzahl = len(b["items"])
            b_beginn_str = b["beginn"].strftime("%H:%M")
            b_ende_str = b["ende"].strftime("%H:%M")

            details_lines = [
                f"{it['beginn'].strftime('%H:%M')}–{it['ende'].strftime('%H:%M')}: {it['text']}"
                for it in b["items"]
            ]
            tooltip = "\n".join(details_lines)

            if anzahl == 1:
                titel = b["items"][0]["text"]
                badge_label = "FSM"
            else:
                titel = f"{anzahl} Fahrstunden"
                badge_label = f"FSM • {anzahl}x"

            eintraege.append({
                "art": "fahrstundenblock",
                "zeit": b["beginn"],
                "beginn_uhrzeit": b_beginn_str,
                "ende_uhrzeit": b_ende_str,
                "anzahl": anzahl,
                "titel": titel,
                "badge_label": badge_label,
                "details_tooltip": tooltip,
                "ist_fsm": True,
                "ist_block": anzahl > 1,
            })

        for s in sonstige_sperren:
            s_beginn = timezone.localtime(s.beginn)
            s_ende = timezone.localtime(s.ende)
            ist_ganztaegig = (s.ende - s.beginn).total_seconds() >= 82800 or (
                s.beginn.date() < tag < s.ende.date()
            )
            eintraege.append(
                {
                    "art": "sperre",
                    "zeit": s.beginn,
                    "beginn_uhrzeit": s_beginn.strftime("%H:%M") if not ist_ganztaegig else "",
                    "ende_uhrzeit": s_ende.strftime("%H:%M") if not ist_ganztaegig else "",
                    "ist_ganztaegig": ist_ganztaegig,
                    "ist_fsm": bool(s.fsm_id),
                    "titel": s.grund or ("FSM-Termin" if s.fsm_id else "Sperrzeit"),
                    "sperre": s,
                }
            )

        eintraege.sort(key=lambda x: (0 if x.get("ist_ganztaegig") else 1, x["zeit"]))

        tage.append(
            {
                "datum": tag,
                "kurz": WOCHENTAG_KURZ[tag.weekday()],
                "label": date_format(tag, "j. F"),
                "ist_heute": tag == heute,
                "ist_vergangen": tag < heute,
                "feiertag": feiertage.get(tag),
                "termine": tages_termine,
                "eintraege": eintraege,
                "sperren_count": len(tages_sperren),
                "fahrstunden_count": len(fahrstunden_sperren),
                "bloecke_count": len(bloecke),
                "frei": sum(1 for t in tages_termine if t.status == Termin.Status.FREI),
                "gebucht": sum(
                    1
                    for t in tages_termine
                    if t.status in (Termin.Status.GEBUCHT, Termin.Status.RESERVIERT)
                ),
            }
        )

    planungs_form = TagesplanungForm(
        initial={"fahrlehrer": fahrlehrer, "tag": max(heute, start_tag)}
    )
    planungs_form.fields["fahrlehrer"].queryset = erlaubt
    sperr_form = SperrzeitForm(initial={"fahrlehrer": fahrlehrer})
    sperr_form.fields["fahrlehrer"].queryset = erlaubt

    return render(
        request,
        "staff/tagesplanung.html",
        {
            "fahrlehrer": fahrlehrer,
            "alle_fahrlehrer": erlaubt,
            "start_tag": start_tag,
            "end_tag": end_tag,
            "montag": start_tag,
            "sonntag": end_tag,
            "vorherige_woche": start_tag - dt.timedelta(days=7),
            "naechste_woche": start_tag + dt.timedelta(days=7),
            "heute": heute,
            "diese_woche": heute,
            "tage": tage,
            "sperrzeiten": sperren,
            "planungs_form": planungs_form,
            "sperr_form": sperr_form,
            "terminarten": Terminart.objects.filter(aktiv=True),
            # Der Horizont gilt fahrschulweit. Stünde hier `fahrlehrer
            # .horizont_wochen`, nennte die Seite eine Zahl, nach der niemand
            # plant.
            "horizont_wochen": FahrschulEinstellungen.get_solo().horizont_wochen
            or settings.DEFAULT_HORIZON_WEEKS,
        },
    )

@mitarbeiter
@require_POST
def termine_anlegen(request):
    form = TagesplanungForm(request.POST)
    form.fields["fahrlehrer"].queryset = _erlaubte_fahrlehrer(request.user)

    if form.is_valid():
        daten = form.cleaned_data
        neue, uebersprungen = termine_manuell_anlegen(
            daten["fahrlehrer"],
            daten["terminart"],
            daten["tag"],
            daten["von"],
            daten["bis"],
            notiz=daten.get("notiz", ""),
        )
        if neue:
            messages.success(
                request,
                f"{len(neue)} Termine am {date_format(daten['tag'], 'j. F Y')} angelegt.",
            )
        if uebersprungen:
            messages.info(
                request,
                f"{uebersprungen} Zeitfenster waren bereits belegt und wurden übersprungen.",
            )
        if not neue and not uebersprungen:
            messages.warning(request, "In diesem Zeitfenster passt kein einziger Termin.")
        ziel_woche = daten["tag"]
        ziel_fahrlehrer = daten["fahrlehrer"].slug
    else:
        for feld, fehler in form.errors.items():
            messages.error(request, f"{feld}: {'; '.join(fehler)}")
        ziel_woche = timezone.localdate()
        ziel_fahrlehrer = request.POST.get("fahrlehrer_slug", "")

    ziel = f"{reverse('termine:tagesplanung')}?woche={ziel_woche:%Y-%m-%d}"
    if ziel_fahrlehrer:
        ziel += f"&fahrlehrer={ziel_fahrlehrer}"
    return redirect(ziel)

@mitarbeiter
@require_POST
def kollision_anlegen(request):
    """Legt einen einzigen Termin bei einem Alternativ-Fahrlehrer an.

    Kommt aus dem Dashboard-Banner: Der Slot der Kollision wird bei einem
    anderen Fahrlehrer freigegeben, ohne dass man die Tagesplanung von Hand
    füttern muss. Nach dem Anlegen gilt die Kollision als gelöst.
    """
    fahrlehrer = get_object_or_404(
        Fahrlehrer, pk=request.POST.get("fahrlehrer"), aktiv=True
    )
    if fahrlehrer not in _erlaubte_fahrlehrer(request.user):
        raise PermissionDenied("Kein Zugriff auf diesen Fahrlehrer.")
    try:
        tag = dt.date.fromisoformat(request.POST.get("tag", ""))
        von = dt.time.fromisoformat(request.POST.get("von", ""))
        bis = dt.time.fromisoformat(request.POST.get("bis", ""))
        terminart = Terminart.objects.get(pk=request.POST.get("terminart"), aktiv=True)
    except (ValueError, Terminart.DoesNotExist):
        messages.error(request, "Ungültige Kollisionsdaten – bitte Seite neu laden.")
        return redirect(_dashboard_ziel(request))

    neue, uebersprungen = termine_manuell_anlegen(
        fahrlehrer, terminart, tag, von, bis
    )
    if neue:
        messages.success(
            request,
            f"Termin am {date_format(tag, 'D, j. M Y')} "
            f"{von:%H:%M}–{bis:%H:%M} bei {fahrlehrer.name} angelegt.",
        )
    else:
        messages.warning(
            request,
            f"Slot bei {fahrlehrer.name} war nicht frei"
            + (" (bereits belegt)." if uebersprungen else "."),
        )
    return redirect(_dashboard_ziel(request))

@mitarbeiter
@require_POST
def kollision_ignorieren(request):
    """Blendet genau dieses Kollisions-Vorkommen einmalig aus dem Banner aus.

    Wiederkehrende Kollisionen (jede Woche gleiche Sperrzeit) gehören in die
    Rhythmus-Regel geändert, nicht hier ignoriert – der Eintrag gilt nur für
    das konkrete Datum.
    """
    fahrlehrer = get_object_or_404(Fahrlehrer, pk=request.POST.get("fahrlehrer"))
    if fahrlehrer not in _erlaubte_fahrlehrer(request.user):
        raise PermissionDenied("Kein Zugriff auf diesen Fahrlehrer.")
    try:
        tag = dt.date.fromisoformat(request.POST.get("tag", ""))
        von = dt.time.fromisoformat(request.POST.get("von", ""))
        bis = dt.time.fromisoformat(request.POST.get("bis", ""))
        terminart = Terminart.objects.get(pk=request.POST.get("terminart"), aktiv=True)
    except (ValueError, Terminart.DoesNotExist):
        messages.error(request, "Ungültige Kollisionsdaten – bitte Seite neu laden.")
        return redirect(_dashboard_ziel(request))

    KollisionsIgnorier.objects.get_or_create(
        fahrlehrer=fahrlehrer,
        tag=tag,
        beginn=von,
        ende=bis,
        terminart=terminart,
    )
    messages.info(request, f"Kollision am {date_format(tag, 'D, j. M Y')} ignoriert.")
    return redirect(_dashboard_ziel(request))

@mitarbeiter
@require_POST
def kollision_ignorier_rueckgangig(request, pk: int):
    """Nimmt ein einmaliges Ignorieren zurück – der Eintrag verschwindet wieder."""
    ignorier = get_object_or_404(
        KollisionsIgnorier,
        pk=pk,
        fahrlehrer__in=_erlaubte_fahrlehrer(request.user),
    )
    ignorier.delete()
    messages.info(request, "Ignorieren zurückgenommen – die Kollision erscheint wieder.")
    # Ohne aktiven Filter („Alle Fahrlehrer") zurück zur Übersicht, sonst zum
    # Fahrlehrer des Eintrags – die Aktion könnte aus dessen Sicht kommen.
    if request.POST.get("fahrlehrer_slug"):
        return redirect(_dashboard_ziel(request))
    return redirect(_dashboard_ziel(request, fahrlehrer=ignorier.fahrlehrer))

@mitarbeiter
@require_POST
def sperrzeit_anlegen(request):
    form = SperrzeitForm(request.POST)
    form.fields["fahrlehrer"].queryset = _erlaubte_fahrlehrer(request.user)

    if form.is_valid():
        daten = form.cleaned_data
        sperre = Sperrzeit.objects.create(
            fahrlehrer=daten["fahrlehrer"],
            beginn=lokal(daten["von_tag"], dt.time.min),
            ende=lokal(daten["bis_tag"], dt.time.max),
            grund=daten.get("grund", ""),
            # Das Formularfeld ist optional und liefert dann "" – kein gültiger
            # Wert für ein Auswahlfeld. `or` statt eines Vorgabewerts in `get`.
            typ=daten.get("typ") or Sperrzeit.Typ.SONSTIGE,
        )
        # Freie Termine im gesperrten Zeitraum verschwinden sofort aus dem Angebot.
        geloescht, entfallen = termine_entfernen(
            Termin.objects.filter(
                fahrlehrer=sperre.fahrlehrer,
                status=Termin.Status.FREI,
                beginn__lt=sperre.ende,
                ende__gt=sperre.beginn,
            )
        )
        entfernt = geloescht + len(entfallen)
        betroffen = Buchung.objects.filter(
            termin__fahrlehrer=sperre.fahrlehrer,
            termin__beginn__lt=sperre.ende,
            termin__ende__gt=sperre.beginn,
            status__in=Buchung.AKTIVE_STATUS,
        ).count()

        if getattr(settings, "FSM_SYNC_ENABLED", False) and sperre.fahrlehrer.fsm_sync_aktiv and sperre.fahrlehrer.fsm_id:
            from ..services import fsm_sync

            transaction.on_commit(lambda: fsm_sync.async_buche_sperrzeit_in_fsm(sperre))

        messages.success(request, f"Sperrzeit eingetragen, {entfernt} freie Termine entfernt.")
        if betroffen:
            messages.warning(
                request,
                f"Achtung: In diesem Zeitraum liegen {betroffen} bereits gebuchte Termine. "
                "Bitte sagen Sie diese von Hand ab.",
            )
        ziel_fahrlehrer = daten["fahrlehrer"].slug
    else:
        for feld, fehler in form.errors.items():
            messages.error(request, f"{feld}: {'; '.join(fehler)}")
        ziel_fahrlehrer = request.POST.get("fahrlehrer_slug", "")

    ziel = reverse("termine:tagesplanung")
    if ziel_fahrlehrer:
        ziel += f"?fahrlehrer={ziel_fahrlehrer}"
    return redirect(ziel)

@mitarbeiter
@require_POST
def termin_loeschen(request, pk: int):
    termin = get_object_or_404(Termin, pk=pk, fahrlehrer__in=_erlaubte_fahrlehrer(request.user))
    if termin.status != Termin.Status.FREI:
        messages.error(
            request,
            "Dieser Termin ist belegt. Bitte stornieren Sie zuerst die Buchung.",
        )
    else:
        tag = termin.tag
        termine_entfernen(Termin.objects.filter(pk=termin.pk))
        messages.success(request, f"Termin am {date_format(tag, 'j. F')} entfernt.")
    return redirect(_sicheres_ziel(request, "termine:tagesplanung"))

@mitarbeiter
@require_POST
def generieren(request):
    """Stößt den Slot-Generator von Hand an."""
    fahrlehrer, _ = _gewaehlter_fahrlehrer(request)
    slug = request.POST.get("fahrlehrer")
    if slug:
        fahrlehrer = _erlaubte_fahrlehrer(request.user).filter(slug=slug).first()

    if fahrlehrer is None:
        messages.error(request, "Kein Fahrlehrer ausgewählt.")
        return redirect("termine:tagesplanung")

    bericht = generiere_termine(fahrlehrer)
    messages.success(
        request,
        f"Terminplanung für {fahrlehrer.name} bis {date_format(bericht.bis, 'j. F Y')}: "
        f"{bericht.als_text()}.",
    )
    return redirect(f"/intern/planung/?fahrlehrer={fahrlehrer.slug}")
