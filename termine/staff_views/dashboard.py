"""Dashboard des internen Bereichs."""

from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict

from django.db.models import Count, Q
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from ..models import (
    Buchung,
    Fahrlehrer,
    FahrschulEinstellungen,
    KollisionsIgnorier,
    Sperrzeit,
    Termin,
)
from ..services.feiertage import feiertage_im_zeitraum
from ..services.planung import (
    finde_kollisionen_rhythmus_regeln,
    lokal,
)
from .common import _datum_aus_get, _erlaubte_fahrlehrer, _monat_aus_get, _monatsgrenzen, mitarbeiter


@mitarbeiter
def dashboard(request):
    erlaubt = _erlaubte_fahrlehrer(request.user)
    alle_fahrlehrer = list(erlaubt.order_by("reihenfolge", "name"))

    gewaehlter_slug = request.GET.get("fahrlehrer", "")
    gewaehlter_fahrlehrer = erlaubt.filter(slug=gewaehlter_slug).first() if gewaehlter_slug else None

    if gewaehlter_fahrlehrer:
        ziel_fahrlehrer = [gewaehlter_fahrlehrer]
    else:
        ziel_fahrlehrer = alle_fahrlehrer

    ziel_pks = [fl.pk for fl in ziel_fahrlehrer]

    jetzt = timezone.now()
    heute = timezone.localdate()

    # Monat aus GET
    jahr, monat = _monat_aus_get(request)
    try:
        gitter_von, gitter_bis = _monatsgrenzen(jahr, monat)
    except (calendar.IllegalMonthError, ValueError):
        jahr, monat = heute.year, heute.month
        gitter_von, gitter_bis = _monatsgrenzen(jahr, monat)

    erster_im_monat = dt.date(jahr, monat, 1)
    vorheriger = erster_im_monat - dt.timedelta(days=1)
    naechster = (erster_im_monat + dt.timedelta(days=32)).replace(day=1)

    # Alle relevanten Termine im sichtbaren Monatszeitraum
    termine_im_monat = (
        Termin.objects.filter(
            fahrlehrer__in=ziel_pks,
            beginn__gte=lokal(gitter_von, dt.time.min),
            beginn__lte=lokal(gitter_bis, dt.time.max),
            status__in=[Termin.Status.FREI, Termin.Status.RESERVIERT, Termin.Status.GEBUCHT],
        )
        .select_related("fahrlehrer", "terminart")
        .prefetch_related("buchungen")
    )

    termine_pro_tag = defaultdict(list)
    stats_pro_tag = defaultdict(lambda: {"frei": 0, "gebucht": 0, "offen": 0, "gesamt": 0})

    for t in termine_im_monat:
        d = timezone.localtime(t.beginn).date()
        termine_pro_tag[d].append(t)
        stats_pro_tag[d]["gesamt"] += 1
        if t.status == Termin.Status.FREI:
            stats_pro_tag[d]["frei"] += 1
        elif t.status == Termin.Status.GEBUCHT:
            stats_pro_tag[d]["gebucht"] += 1
        elif t.status == Termin.Status.RESERVIERT:
            stats_pro_tag[d]["offen"] += 1

    # Gewählter Tag
    gewaehlter_tag = _datum_aus_get(request, "tag")
    if gewaehlter_tag is None:
        if erster_im_monat <= heute <= gitter_bis:
            gewaehlter_tag = heute
        else:
            tage_mit_terminen = sorted([d for d in termine_pro_tag.keys() if d.month == monat])
            gewaehlter_tag = tage_mit_terminen[0] if tage_mit_terminen else erster_im_monat

    # Monatsgitter
    kal = calendar.Calendar(firstweekday=0)
    wochen = kal.monthdatescalendar(jahr, monat)

    bundesland = gewaehlter_fahrlehrer.bundesland if gewaehlter_fahrlehrer else FahrschulEinstellungen.get_solo().bundesland
    feiertage = feiertage_im_zeitraum(bundesland, gitter_von, gitter_bis)

    gitter = []
    for woche in wochen:
        zeile = []
        for tag in woche:
            st = stats_pro_tag[tag]
            zeile.append({
                "datum": tag,
                "tag": tag.day,
                "im_monat": tag.month == monat,
                "ist_heute": tag == heute,
                "ist_vergangen": tag < heute,
                "feiertag": feiertage.get(tag),
                "frei_anzahl": st["frei"],
                "gebucht_anzahl": st["gebucht"],
                "offen_anzahl": st["offen"],
                "gesamt_anzahl": st["gesamt"],
                "hat_termine": st["gesamt"] > 0,
                "hat_frei": st["frei"] > 0,
                "hat_gebucht": (st["gebucht"] + st["offen"]) > 0,
                "ausgewaehlt": tag == gewaehlter_tag,
            })
        gitter.append(zeile)

    # Termine am gewählten Tag – aus dem bereits geladenen Monatsbestand,
    # statt dieselben Zeilen noch einmal aus der DB zu holen.
    tages_termine = []
    for termin in termine_im_monat:
        if timezone.localtime(termin.beginn).date() != gewaehlter_tag:
            continue
        # Aus dem prefetch, nicht per Einzelabfrage – und mit derselben
        # Abgrenzung wie `Termin.aktive_buchung`: Eine verfallene Reservierung
        # ist keine Buchung mehr und stünde sonst als Kundenname am freien Slot.
        aktive_buchung = next(
            (b for b in termin.buchungen.all() if b.status in Buchung.AKTIVE_STATUS), None
        )
        verfallene = [
            b for b in sorted(termin.buchungen.all(), key=lambda x: x.erstellt_am, reverse=True)
            if b.status == Buchung.Status.VERFALLEN
        ]
        letzte_verfallene = verfallene[0] if verfallene else None

        tages_termine.append({
            "termin": termin,
            "buchung": aktive_buchung,
            "verfallene_buchung": letzte_verfallene,
        })
    tages_termine.sort(key=lambda e: (e["termin"].beginn, e["termin"].fahrlehrer.name))

    # Nächste bestätigte Buchungen
    naechste = (
        Buchung.objects.filter(
            status=Buchung.Status.BESTAETIGT,
            termin__fahrlehrer__in=ziel_pks,
            termin__beginn__gte=jetzt,
        )
        .select_related("termin", "termin__terminart", "termin__fahrlehrer")
        .order_by("termin__beginn")[:8]
    )

    # Anstehende verfallene Buchungen (Frist abgelaufen, Kunde kommt evtl. trotzdem)
    anstehende_verfallene = list(
        Buchung.objects.filter(
            status=Buchung.Status.VERFALLEN,
            termin__fahrlehrer__in=ziel_pks,
            termin__beginn__gte=jetzt,
        )
        .select_related("termin", "termin__terminart", "termin__fahrlehrer")
        .order_by("termin__beginn")[:10]
    )

    # KPIs
    kpi_frei = Termin.objects.filter(
        fahrlehrer__in=ziel_pks,
        status=Termin.Status.FREI,
        beginn__gte=jetzt,
    ).count()

    kpi_stats = Buchung.objects.filter(
        termin__fahrlehrer__in=ziel_pks,
        termin__beginn__gte=jetzt,
    ).aggregate(
        kpi_gebucht=Count("pk", filter=Q(status=Buchung.Status.BESTAETIGT)),
        kpi_offen=Count("pk", filter=Q(status=Buchung.Status.OFFEN)),
    )
    kpi_gebucht = kpi_stats["kpi_gebucht"]
    kpi_offen = kpi_stats["kpi_offen"]

    kollisionen = finde_kollisionen_rhythmus_regeln(ziel_fahrlehrer)
    ignorierte_kollisionen = list(
        KollisionsIgnorier.objects.filter(
            fahrlehrer__in=ziel_pks,
            tag__gte=heute,
        ).select_related("fahrlehrer", "terminart")
    )

    querystring = f"fahrlehrer={gewaehlter_slug}" if gewaehlter_slug else ""

    return render(
        request,
        "staff/dashboard.html",
        {
            "alle_fahrlehrer": alle_fahrlehrer,
            "fahrlehrer_auswahl": len(alle_fahrlehrer) > 1,
            "gewaehlter_fahrlehrer": gewaehlter_fahrlehrer,
            "gewaehlter_slug": gewaehlter_slug,
            "querystring": querystring,
            "jahr": jahr,
            "monat": monat,
            "monatsname": date_format(erster_im_monat, "F Y"),
            "vorheriger_monat": f"{vorheriger:%Y-%m}",
            "naechster_monat": f"{naechster:%Y-%m}",
            "gitter": gitter,
            "gewaehlter_tag": gewaehlter_tag,
            "tages_termine": tages_termine,
            "naechste_buchungen": naechste,
            "anstehende_verfallene": anstehende_verfallene,
            "kpi_frei": kpi_frei,
            "kpi_gebucht": kpi_gebucht,
            "kpi_offen": kpi_offen,
            "kollisionen": kollisionen,
            "ignorierte_kollisionen": ignorierte_kollisionen,
        },
    )

def _ist_fahrstunde_sperre(s: Sperrzeit) -> bool:
    """Prüft, ob eine Sperrzeit eine FSM-Fahrstunde darstellt, die in Blöcken gebündelt werden soll."""
    ist_fsm = (
        s.herkunft == Sperrzeit.Herkunft.FSM
        or bool(s.fsm_id)
        or (s.grund or "").strip().startswith("FSM:")
    )
    if not ist_fsm:
        return False
    dauer_sec = (s.ende - s.beginn).total_seconds()
    if dauer_sec >= 82800:
        return False
    grund_low = (s.grund or "").strip().lower()
    if (
        "privat" in grund_low
        or "urlaub" in grund_low
        or "krank" in grund_low
        or "theorieunterricht (raum belegt)" in grund_low
    ):
        return False
    return True

def _dashboard_ziel(request, fahrlehrer=None):
    """Nach Kollisions-Aktionen zurück aufs Dashboard – mit gewähltem Fahrlehrer.

    Ohne aktiven Filter („Alle Fahrlehrer") bleibt es dabei; ein mitgegebener
    Einzelslug wird über `fahrlehrer_slug` bevorzugt.
    """
    ziel = reverse("termine:dashboard")
    if fahrlehrer is None:
        slug = request.POST.get("fahrlehrer_slug", "")
        if slug:
            try:
                fahrlehrer = Fahrlehrer.objects.get(slug=slug)
            except Fahrlehrer.DoesNotExist:
                fahrlehrer = None
    if fahrlehrer:
        ziel += f"?fahrlehrer={fahrlehrer.slug}"
    return ziel
