"""Buchungen: Liste, Detail, Verschieben, Einbuchen, Absagen, Historie."""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from ..models import (
    Buchung,
    Termin,
)
from ..services import buchung as buchungs_service
from ..services.planung import (
    lokal,
)
from .common import _erlaubte_fahrlehrer, _sicheres_ziel, mitarbeiter


@mitarbeiter
def buchungsliste(request):
    erlaubt = _erlaubte_fahrlehrer(request.user)
    status = request.GET.get("status", "aktiv")

    buchungen = Buchung.objects.filter(termin__fahrlehrer__in=erlaubt).select_related(
        "termin", "termin__terminart", "termin__fahrlehrer"
    )
    if status == "aktiv":
        buchungen = buchungen.filter(
            status__in=Buchung.AKTIVE_STATUS, termin__beginn__gte=timezone.now()
        ).order_by("termin__beginn")
    elif status == "vergangen":
        buchungen = buchungen.filter(termin__beginn__lt=timezone.now()).order_by(
            "-termin__beginn"
        )
    elif status in dict(Buchung.Status.choices):
        buchungen = buchungen.filter(status=status).order_by("-erstellt_am")
    else:
        buchungen = buchungen.order_by("-erstellt_am")

    return render(
        request,
        "staff/buchungen.html",
        {
            "buchungen": buchungen[:200],
            "status": status,
            "status_optionen": Buchung.Status.choices,
        },
    )

@mitarbeiter
def buchung_detail(request, pk: int):
    """Einzelansicht einer Buchung mit Kundendaten, Historie, Verschieben und Absagen."""
    erlaubt = _erlaubte_fahrlehrer(request.user)
    buchung = get_object_or_404(
        Buchung.objects.select_related("termin", "termin__fahrlehrer", "termin__terminart"),
        pk=pk,
        termin__fahrlehrer__in=erlaubt,
    )

    # Frühere/weitere Buchungen dieses Kunden (per E-Mail, Telefon oder email_hash)
    andere_filter = Q()
    if buchung.email and buchung.email != "Gelöscht":
        andere_filter |= Q(email__iexact=buchung.email)
    if buchung.email_hash:
        andere_filter |= Q(email_hash=buchung.email_hash)
    if buchung.telefon and buchung.telefon != "Gelöscht":
        andere_filter |= Q(telefon=buchung.telefon)

    andere_buchungen = []
    if andere_filter:
        andere_buchungen = list(
            Buchung.objects.filter(andere_filter)
            .exclude(pk=buchung.pk)
            .select_related("termin", "termin__fahrlehrer", "termin__terminart")
            .order_by("-termin__beginn")
        )

    # Freie Termine für Verschiebung (ab jetzt, für alle erlaubten Fahrlehrer)
    jetzt = timezone.now()
    freie_termine = list(
        Termin.objects.filter(
            fahrlehrer__in=erlaubt,
            status=Termin.Status.FREI,
            beginn__gte=jetzt,
        )
        .select_related("fahrlehrer", "terminart")
        .order_by("beginn")[:60]
    )

    original_termin_verfuegbar = (
        buchung.status == Buchung.Status.VERFALLEN
        and buchung.termin.status == Termin.Status.FREI
        and buchung.termin.beginn > jetzt
        and not buchung.termin.ist_gesperrt()
    )

    return render(
        request,
        "staff/buchung_detail.html",
        {
            "buchung": buchung,
            "andere_buchungen": andere_buchungen,
            "freie_termine": freie_termine,
            "original_termin_verfuegbar": original_termin_verfuegbar,
        },
    )

@mitarbeiter
@require_POST
def buchung_verschieben(request, pk: int):
    erlaubt = _erlaubte_fahrlehrer(request.user)
    buchung = get_object_or_404(Buchung, pk=pk, termin__fahrlehrer__in=erlaubt)

    neuer_termin_id = request.POST.get("neuer_termin_id")
    if not neuer_termin_id:
        messages.error(request, "Bitte wählen Sie einen Ziel-Termin aus.")
        return redirect("termine:buchung_detail", pk=buchung.pk)

    try:
        # Rückgabe übernehmen: `verschieben` arbeitet auf einer eigenen,
        # gesperrten Instanz. Die hiesige zeigte sonst weiter auf den alten
        # Termin – und die Erfolgsmeldung nennte das alte Datum.
        buchung = buchungs_service.verschieben(buchung, int(neuer_termin_id))
        messages.success(
            request,
            f"Termin für {buchung.name} erfolgreich auf {date_format(buchung.termin.beginn_lokal, 'SHORT_DATE_FORMAT')}, {buchung.termin.beginn_lokal:%H:%M} Uhr verschoben.",
        )
    except buchungs_service.BuchungsFehler as exc:
        messages.error(request, str(exc))

    return redirect("termine:buchung_detail", pk=buchung.pk)

@mitarbeiter
@require_POST
def buchung_wieder_einbuchen(request, pk: int):
    erlaubt = _erlaubte_fahrlehrer(request.user)
    buchung = get_object_or_404(Buchung, pk=pk, termin__fahrlehrer__in=erlaubt)

    if buchung.status != Buchung.Status.VERFALLEN:
        messages.error(request, "Nur verfallene Buchungen können wieder eingebucht werden.")
        return redirect("termine:buchung_detail", pk=buchung.pk)

    neuer_termin_id = request.POST.get("ziel_termin_id")
    ziel_termin = None
    if neuer_termin_id:
        try:
            ziel_termin = Termin.objects.get(pk=int(neuer_termin_id), fahrlehrer__in=erlaubt)
        except (Termin.DoesNotExist, ValueError):
            messages.error(request, "Der gewählte Termin existiert nicht oder ist nicht erlaubt.")
            return redirect("termine:buchung_detail", pk=buchung.pk)

    try:
        buchung = buchungs_service.verfallene_buchung_einbuchen(buchung, ziel_termin=ziel_termin)
        messages.success(
            request,
            f"Buchung für {buchung.name} am {date_format(buchung.termin.beginn_lokal, 'SHORT_DATE_FORMAT')}, {buchung.termin.beginn_lokal:%H:%M} Uhr erfolgreich eingebucht. Die Bestätigungsmail wurde an den Kunden verschickt.",
        )
    except (buchungs_service.BuchungsFehler, buchungs_service.TerminNichtVerfuegbar) as exc:
        messages.error(request, str(exc))

    return redirect("termine:buchung_detail", pk=buchung.pk)

@mitarbeiter
@require_POST
def buchung_absagen(request, pk: int):
    buchung = get_object_or_404(
        Buchung, pk=pk, termin__fahrlehrer__in=_erlaubte_fahrlehrer(request.user)
    )
    if not buchung.ist_aktiv:
        messages.info(request, "Diese Buchung war bereits beendet.")
    else:
        buchungs_service.stornieren(buchung, von="fahrschule")
        messages.success(
            request, f"Buchung von {buchung.name} abgesagt, der Kunde wurde per E-Mail informiert."
        )
    return redirect(_sicheres_ziel(request, "termine:buchungen"))

@mitarbeiter
def historie(request):
    """Chronologischer Aktivitäts-Feed: Buchungen, Bestätigungen, Stornierungen und verfallene Termine."""
    erlaubt = _erlaubte_fahrlehrer(request.user, auch_inaktive=True)
    alle_fahrlehrer = list(erlaubt.order_by("reihenfolge", "name"))

    gewaehlter_slug = request.GET.get("fahrlehrer", "")
    gewaehlter_fl = erlaubt.filter(slug=gewaehlter_slug).first() if gewaehlter_slug else None
    ziel_fahrlehrer = [gewaehlter_fl] if gewaehlter_fl else alle_fahrlehrer

    aktion = request.GET.get("aktion", "alle")
    zeitraum = request.GET.get("zeitraum", "30tage")
    suchbegriff = request.GET.get("q", "").strip()

    jetzt = timezone.now()
    heute = timezone.localdate()
    heute_start = lokal(heute, dt.time.min)

    # Basis-Query für Buchungen
    basis_buchungen = Buchung.objects.filter(
        termin__fahrlehrer__in=ziel_fahrlehrer
    ).select_related("termin", "termin__fahrlehrer", "termin__terminart")

    # KPI-Berechnungen (letzte 7 Tage & Gesamt)
    woche_start = jetzt - dt.timedelta(days=7)
    kpi_buchungen_woche = Buchung.objects.filter(
        termin__fahrlehrer__in=ziel_fahrlehrer,
        erstellt_am__gte=woche_start,
    ).count()
    kpi_stornos_woche = Buchung.objects.filter(
        termin__fahrlehrer__in=ziel_fahrlehrer,
        storniert_am__gte=woche_start,
    ).count()
    kpi_gesamt_buchungen = Buchung.objects.filter(termin__fahrlehrer__in=ziel_fahrlehrer).count()
    kpi_gesamt_stornos = Buchung.objects.filter(
        termin__fahrlehrer__in=ziel_fahrlehrer, status=Buchung.Status.STORNIERT
    ).count()
    kpi_stornoquote = (
        round((kpi_gesamt_stornos / kpi_gesamt_buchungen * 100), 1)
        if kpi_gesamt_buchungen > 0
        else 0.0
    )
    kpi_offen = Buchung.objects.filter(
        termin__fahrlehrer__in=ziel_fahrlehrer,
        status=Buchung.Status.OFFEN,
        reserviert_bis__gt=jetzt,
    ).count()

    # Zeitraum-Filtergrenze
    if zeitraum == "heute":
        zeit_grenze = heute_start
    elif zeitraum == "7tage":
        zeit_grenze = jetzt - dt.timedelta(days=7)
    elif zeitraum == "30tage":
        zeit_grenze = jetzt - dt.timedelta(days=30)
    else:
        zeit_grenze = None

    ereignisse = []

    # 1. Buchungs-Ereignisse sammeln
    if aktion in ("alle", "buchung"):
        qs_b = basis_buchungen.exclude(
            status__in=[Buchung.Status.STORNIERT, Buchung.Status.VERFALLEN]
        )
        if zeit_grenze:
            qs_b = qs_b.filter(erstellt_am__gte=zeit_grenze)
        if suchbegriff:
            qs_b = qs_b.filter(
                Q(name__icontains=suchbegriff)
                | Q(email__icontains=suchbegriff)
                | Q(telefon__icontains=suchbegriff)
                | Q(nachricht__icontains=suchbegriff)
            )

        for b in qs_b.order_by("-erstellt_am")[:150]:
            ist_bestaetigt = b.status == Buchung.Status.BESTAETIGT
            ist_offen = b.status == Buchung.Status.OFFEN
            ereignisse.append(
                {
                    "art": "buchung",
                    "zeitpunkt": b.erstellt_am,
                    "titel": (
                        f"Buchung bestätigt: {b.name}"
                        if ist_bestaetigt
                        else (f"Buchung angefragt: {b.name}" if ist_offen else f"Buchung: {b.name}")
                    ),
                    "badge": "Bestätigt" if ist_bestaetigt else ("Offen" if ist_offen else "Gebucht"),
                    "badge_class": "status--gebucht" if ist_bestaetigt else "status--reserviert",
                    "buchung": b,
                    "termin": b.termin,
                    "fahrlehrer": b.termin.fahrlehrer,
                    "terminart": b.termin.terminart.name,
                    "termin_beginn": b.termin.beginn,
                    "termin_ende": b.termin.ende,
                    "kunde_name": b.name,
                    "kunde_email": b.email,
                    "kunde_telefon": b.telefon,
                    "klasse": b.fuehrerscheinklasse,
                    "nachricht": b.nachricht,
                    "status": b.status,
                }
            )

    # 2. Stornierungs-Ereignisse sammeln
    if aktion in ("alle", "storno"):
        qs_s = basis_buchungen.filter(
            Q(status=Buchung.Status.STORNIERT) | Q(storniert_am__isnull=False)
        )
        if zeit_grenze:
            qs_s = qs_s.filter(storniert_am__gte=zeit_grenze)
        if suchbegriff:
            qs_s = qs_s.filter(
                Q(name__icontains=suchbegriff)
                | Q(email__icontains=suchbegriff)
                | Q(telefon__icontains=suchbegriff)
                | Q(nachricht__icontains=suchbegriff)
            )

        for b in qs_s.order_by("-storniert_am")[:150]:
            storno_zeit = b.storniert_am or b.erstellt_am
            durch = "Online durch Kunde" if b.storniert_von == "kunde" else "Fahrschule / Büro"
            ereignisse.append(
                {
                    "art": "storno",
                    "zeitpunkt": storno_zeit,
                    "titel": f"Buchung storniert: {b.name}",
                    "badge": "Storniert",
                    "badge_class": "status--storniert",
                    "storniert_von": durch,
                    "buchung": b,
                    "termin": b.termin,
                    "fahrlehrer": b.termin.fahrlehrer,
                    "terminart": b.termin.terminart.name,
                    "termin_beginn": b.termin.beginn,
                    "termin_ende": b.termin.ende,
                    "kunde_name": b.name,
                    "kunde_email": b.email,
                    "kunde_telefon": b.telefon,
                    "klasse": b.fuehrerscheinklasse,
                    "nachricht": b.nachricht,
                    "status": b.status,
                }
            )

    # 3. Verfallene Reservierungen sammeln
    if aktion in ("alle", "verfallen"):
        qs_v = basis_buchungen.filter(status=Buchung.Status.VERFALLEN)
        if zeit_grenze:
            qs_v = qs_v.filter(
                Q(verfallen_am__gte=zeit_grenze)
                | Q(verfallen_am__isnull=True, erstellt_am__gte=zeit_grenze)
            )
        if suchbegriff:
            qs_v = qs_v.filter(
                Q(name__icontains=suchbegriff)
                | Q(email__icontains=suchbegriff)
                | Q(telefon__icontains=suchbegriff)
            )

        for b in qs_v.order_by("-verfallen_am", "-erstellt_am")[:100]:
            verfall_zeit = b.verfallen_am or b.reserviert_bis or b.erstellt_am
            ereignisse.append(
                {
                    "art": "verfallen",
                    "zeitpunkt": verfall_zeit,
                    "titel": f"Reservierung verfallen: {b.name}",
                    "badge": "Verfallen",
                    "badge_class": "status--entfallen",
                    "buchung": b,
                    "termin": b.termin,
                    "fahrlehrer": b.termin.fahrlehrer,
                    "terminart": b.termin.terminart.name,
                    "termin_beginn": b.termin.beginn,
                    "termin_ende": b.termin.ende,
                    "kunde_name": b.name,
                    "kunde_email": b.email,
                    "kunde_telefon": b.telefon,
                    "klasse": b.fuehrerscheinklasse,
                    "status": b.status,
                }
            )

    # Chronologisch sortieren (neueste zuerst)
    ereignisse.sort(key=lambda x: x["zeitpunkt"] or jetzt, reverse=True)

    # Paginierung (20 Einträge pro Seite)
    paginator = Paginator(ereignisse, 20)
    seiten_nr = request.GET.get("seite", 1)
    try:
        seite = paginator.page(seiten_nr)
    except PageNotAnInteger:
        seite = paginator.page(1)
    except EmptyPage:
        seite = paginator.page(paginator.num_pages)

    # Filter-Parameter ohne 'seite' für saubere Pagination-Links
    query_dict = request.GET.copy()
    if "seite" in query_dict:
        del query_dict["seite"]
    filter_query = query_dict.urlencode()

    return render(
        request,
        "staff/historie.html",
        {
            "ereignisse": seite.object_list,
            "page_obj": seite,
            "paginator": paginator,
            "filter_query": filter_query,
            "gesamt_anzahl": len(ereignisse),
            "alle_fahrlehrer": alle_fahrlehrer,
            "gewaehlter_slug": gewaehlter_slug,
            "aktion": aktion,
            "zeitraum": zeitraum,
            "q": suchbegriff,
            "kpi_buchungen_woche": kpi_buchungen_woche,
            "kpi_stornos_woche": kpi_stornos_woche,
            "kpi_stornoquote": kpi_stornoquote,
            "kpi_offen": kpi_offen,
        },
    )
