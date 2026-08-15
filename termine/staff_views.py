"""Interner Bereich für Fahrlehrer: Tagesplanung, Regeln, Buchungen, Einstellungen.

Hier steht alles, was die Fahrschule im Betrieb braucht – einschließlich der
Stammdaten, die sie selbst pflegt: Terminarten, die eigenen Einstellungen und
den Planungshorizont. Der Django-Admin bleibt für das zuständig, was darüber
hinausgeht: Benutzerkonten, Verknüpfung von Login und Fahrlehrer, der Blick in
einzelne Datensätze.

Der Zugang hat zwei Stufen und beide stehen genau einmal hier oben:
`mitarbeiter` lässt herein, `inhaber` schränkt auf das ein, was für die ganze
Fahrschule gilt. Welche Daten jemand dann sieht, entscheidet allein
`_erlaubte_fahrlehrer` – verteilte Prüfungen in den Views gibt es bewusst nicht.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import (
    FahrlehrerEinstellungenForm,
    FuehrerscheinklasseForm,
    GlobaleEinstellungenForm,
    RhythmusRegelForm,
    SperrzeitForm,
    TagesplanungForm,
    TerminartForm,
)
from .models import (
    FUEHRERSCHEINKLASSEN,
    WOCHENTAG_KURZ,
    Buchung,
    FahrschulEinstellungen,
    Fahrlehrer,
    Fuehrerscheinklasse,
    RhythmusRegel,
    Sperrzeit,
    Termin,
    Terminart,
    neuer_token,
)
from .services import buchung as buchungs_service
from .services.feiertage import feiertage_im_zeitraum
from .services.fsm_client import FsmClient, FsmError
from .services.planung import (
    finde_kollisionen_rhythmus_regeln,
    generiere_alle,
    generiere_termine,
    lokal,
    termine_entfernen,
    termine_manuell_anlegen,
    vorschau,
)


def mitarbeiter(view):
    """Zugang für Django-Staff und für Benutzer mit Fahrlehrer-Profil."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_staff or hasattr(request.user, "fahrlehrer")):
            raise PermissionDenied("Kein Zugriff auf den internen Bereich.")
        return view(request, *args, **kwargs)

    return wrapper


def inhaber(view):
    """Zusätzliche Stufe für alles, was mehr als den eigenen Kalender betrifft.

    Ein Fahrlehrer pflegt seine eigenen Einstellungen; wer Fahrlehrer anlegt
    oder jemanden aus dem öffentlichen Angebot nimmt, entscheidet für die
    ganze Fahrschule. Das ist der Inhaber – dieselbe Grenze, an der auch der
    Django-Admin hängt.
    """

    @wraps(view)
    @mitarbeiter
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied("Das darf nur die Inhaberin oder der Inhaber.")
        return view(request, *args, **kwargs)

    return wrapper


def _erlaubte_fahrlehrer(user, *, auch_inaktive: bool = False):
    """Staff sieht alle, ein Fahrlehrer nur sich selbst.

    Der tägliche Betrieb blendet Inaktive aus – für sie gibt es nichts zu
    planen. Die Einstellungen müssen sie trotzdem erreichen: Sonst wäre das
    Wegnehmen des Hakens „Aktiv“ eine Einbahnstraße, aus der nur noch der
    Django-Admin herausführt.
    """
    if user.is_staff:
        alle = Fahrlehrer.objects.all()
        return alle if auch_inaktive else alle.filter(aktiv=True)
    return Fahrlehrer.objects.filter(pk=user.fahrlehrer.pk)


def _gewaehlter_fahrlehrer(request, *, auch_inaktive: bool = False):
    erlaubt = _erlaubte_fahrlehrer(request.user, auch_inaktive=auch_inaktive)
    slug = request.GET.get("fahrlehrer")
    if slug:
        gewaehlt = erlaubt.filter(slug=slug).first()
        if gewaehlt:
            return gewaehlt, erlaubt
    return erlaubt.first(), erlaubt


def _montag(tag: dt.date) -> dt.date:
    return tag - dt.timedelta(days=tag.weekday())


def _sicheres_ziel(request, standard: str) -> str:
    """Zurück zur Herkunftsseite – aber nur, wenn sie zu dieser Installation gehört."""
    referer = request.META.get("HTTP_REFERER")
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return referer
    return reverse(standard)


def _datum_aus_get(request, name: str) -> dt.date | None:
    roh = request.GET.get(name)
    if not roh:
        return None
    try:
        return dt.date.fromisoformat(roh)
    except ValueError:
        return None


def _monat_aus_get(request) -> tuple[int, int]:
    """Liest den anzuzeigenden Monat aus der URL, sonst der aktuelle Monat."""
    heute = timezone.localdate()
    roh = request.GET.get("monat")
    if roh:
        try:
            jahr, monat = roh.split("-")
            return int(jahr), int(monat)
        except (ValueError, TypeError):
            pass
    return heute.year, heute.month


def _monatsgrenzen(jahr: int, monat: int) -> tuple[dt.date, dt.date]:
    """Erster und letzter im Monatsgitter sichtbarer Tag (inkl. Nachbarmonate)."""
    wochen = calendar.Calendar(firstweekday=0).monthdatescalendar(jahr, monat)
    return wochen[0][0], wochen[-1][-1]


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

    # Termine am gewählten Tag
    tages_termine_qs = (
        Termin.objects.filter(
            fahrlehrer__in=ziel_pks,
            beginn__gte=lokal(gewaehlter_tag, dt.time.min),
            beginn__lte=lokal(gewaehlter_tag, dt.time.max),
            status__in=[Termin.Status.FREI, Termin.Status.RESERVIERT, Termin.Status.GEBUCHT],
        )
        .select_related("fahrlehrer", "terminart")
        .prefetch_related("buchungen")
        .order_by("beginn", "fahrlehrer__name")
    )

    tages_termine = []
    for termin in tages_termine_qs:
        aktive_buchung = termin.buchungen.exclude(status=Buchung.Status.STORNIERT).first()
        tages_termine.append({
            "termin": termin,
            "buchung": aktive_buchung,
        })

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

    # KPIs
    kpi_frei = Termin.objects.filter(
        fahrlehrer__in=ziel_pks,
        status=Termin.Status.FREI,
        beginn__gte=jetzt,
    ).count()

    kpi_gebucht = Buchung.objects.filter(
        termin__fahrlehrer__in=ziel_pks,
        status=Buchung.Status.BESTAETIGT,
        termin__beginn__gte=jetzt,
    ).count()

    kpi_offen = Buchung.objects.filter(
        termin__fahrlehrer__in=ziel_pks,
        status=Buchung.Status.OFFEN,
        termin__beginn__gte=jetzt,
    ).count()

    kollisionen = finde_kollisionen_rhythmus_regeln(ziel_fahrlehrer)

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
            "kpi_frei": kpi_frei,
            "kpi_gebucht": kpi_gebucht,
            "kpi_offen": kpi_offen,
            "kollisionen": kollisionen,
        },
    )


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
                }
            )

        for s in tages_sperren:
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
            typ=daten.get("typ", Sperrzeit.Typ.SONSTIGE),
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
            from .services import fsm_sync

            transaction.on_commit(lambda: fsm_sync.async_buche_sperrzeit_in_fsm(sperre))

        messages.success(request, f"Sperrzeit eingetragen, {entfernt} freie Termine entfernt.")
        if betroffen:
            messages.warning(
                request,
                f"Achtung: In diesem Zeitraum liegen {betroffen} bereits gebuchte Termine. "
                "Bitte sagen Sie diese von Hand ab.",
            )
    else:
        for feld, fehler in form.errors.items():
            messages.error(request, f"{feld}: {'; '.join(fehler)}")
    return redirect("termine:tagesplanung")


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

    return render(
        request,
        "staff/buchung_detail.html",
        {
            "buchung": buchung,
            "andere_buchungen": andere_buchungen,
            "freie_termine": freie_termine,
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
        buchungs_service.verschieben(buchung, int(neuer_termin_id))
        messages.success(
            request,
            f"Termin für {buchung.name} erfolgreich auf {date_format(buchung.termin.beginn_lokal, 'SHORT_DATE_FORMAT')}, {buchung.termin.beginn_lokal:%H:%M} Uhr verschoben.",
        )
    except buchungs_service.BuchungsFehler as exc:
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

    # Vorschau: welche Termine würde diese Regelkonstellation erzeugen?
    vorschau_tage = []
    if regel is not None:
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
    regel.delete()
    messages.success(
        request,
        f"Regel gelöscht. Bereits gebuchte Termine von {fahrlehrer.name} bleiben bestehen.",
    )
    return redirect("termine:regeln")


# --- Terminarten -----------------------------------------------------------
#
# Bis hierher lagen die Terminarten im Django-Admin, also hinter `is_staff`.
# Damit konnte ein Fahrlehrer zwar planen, aber nicht festlegen, *was* er
# anbietet – und musste für jede neue Beratungsart beim Inhaber anfragen.
# Terminarten sind Stammdaten der Fahrschule, keine persönlichen Daten;
# deshalb darf sie hier jeder pflegen, der den internen Bereich sieht.


@mitarbeiter
def terminartenliste(request):
    terminarten = Terminart.objects.annotate(
        anzahl_termine=Count("termine", distinct=True),
        anzahl_regeln=Count("regeln", distinct=True),
    ).order_by("reihenfolge", "name")
    return render(request, "staff/terminarten.html", {"terminarten": terminarten})


@mitarbeiter
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


@mitarbeiter
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


@mitarbeiter
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


@mitarbeiter
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


@mitarbeiter
@require_POST
def klasse_loeschen(request, pk: int):
    klasse = get_object_or_404(Fuehrerscheinklasse, pk=pk)
    code = klasse.code
    klasse.delete()
    messages.success(request, f"Führerscheinklasse „{code}“ gelöscht.")
    return redirect("termine:klassen")


# --- Einstellungen ---------------------------------------------------------


@mitarbeiter
def einstellungen(request):
    """Was früher nur im Django-Admin einzustellen war.

    Die Auswahl des Fahrlehrers läuft wie in der Tagesplanung über die
    Adresse; das Formular schickt sie deshalb im `action` wieder mit, sonst
    landete ein Inhaber nach dem Speichern beim ersten Fahrlehrer statt bei
    dem, den er gerade bearbeitet hat.
    """
    fahrlehrer, erlaubt = _gewaehlter_fahrlehrer(request, auch_inaktive=True)
    ist_inhaber = request.user.is_staff

    if fahrlehrer is None:
        return render(
            request,
            "staff/einstellungen.html",
            {"fahrlehrer": None, "alle_fahrlehrer": erlaubt, "ist_inhaber": ist_inhaber},
        )

    globale_einst = FahrschulEinstellungen.get_solo()
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", "")

    if request.method == "POST":
        if request.POST.get("form_art") == "global" and ist_inhaber:
            alter_horizont = globale_einst.horizont_wochen
            globale_form = GlobaleEinstellungenForm(request.POST, instance=globale_einst)
            if globale_form.is_valid():
                globale_einst = globale_form.save()
                if globale_einst.horizont_wochen != alter_horizont:
                    _globaler_horizont_nachziehen(request, alter_horizont)
                if is_ajax:
                    return JsonResponse({"ok": True, "nachricht": "Globale Einstellungen automatisch gespeichert."})
                messages.success(request, "Buchungsfenster (global) gespeichert.")
                return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}#tab-buchung")
            if is_ajax:
                return JsonResponse({"ok": False, "fehler": globale_form.errors.as_text()}, status=400)
            form = FahrlehrerEinstellungenForm(instance=fahrlehrer, inhaber=ist_inhaber)
        else:
            globale_form = GlobaleEinstellungenForm(instance=globale_einst)
            form = FahrlehrerEinstellungenForm(
                request.POST, instance=fahrlehrer, inhaber=ist_inhaber
            )
            if form.is_valid():
                fahrlehrer = form.save()
                if is_ajax:
                    return JsonResponse({"ok": True, "nachricht": "Profil-Einstellungen automatisch gespeichert."})
                messages.success(request, "Einstellungen gespeichert.")
                return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}#tab-profil")
            if is_ajax:
                return JsonResponse({"ok": False, "fehler": form.errors.as_text()}, status=400)
    else:
        form = FahrlehrerEinstellungenForm(instance=fahrlehrer, inhaber=ist_inhaber)
        globale_form = GlobaleEinstellungenForm(instance=globale_einst)

    jetzt = timezone.now()
    alle_sperren = list(
        Sperrzeit.objects.filter(fahrlehrer=fahrlehrer, ende__gte=jetzt).order_by("beginn")
    )
    manuelle_sperren = [s for s in alle_sperren if not s.fsm_id]
    fsm_sperren_count = sum(1 for s in alle_sperren if s.fsm_id)
    alle_lehrer_sperren = list(
        Sperrzeit.objects.filter(fahrlehrer__in=erlaubt, ende__gte=jetzt, fsm_id="")
        .select_related("fahrlehrer")
        .order_by("beginn")
    )

    return render(
        request,
        "staff/einstellungen.html",
        {
            "fahrlehrer": fahrlehrer,
            "alle_fahrlehrer": erlaubt,
            "ist_inhaber": ist_inhaber,
            "form": form,
            "globale_form": globale_form,
            "globale_einstellungen": globale_einst,
            "buchbar_bis": timezone.localtime(fahrlehrer.spaetester_start()).date(),
            "sperrzeiten": manuelle_sperren,
            "alle_sperrzeiten": alle_sperren,
            "alle_lehrer_sperren": alle_lehrer_sperren,
            "fsm_sperren_count": fsm_sperren_count,
            "terminarten": Terminart.objects.all(),
        },
    )


def _globaler_horizont_nachziehen(request, alter_horizont: int) -> None:
    """Nach einer Änderung des globalen Horizonts die Termine aller Fahrlehrer anpassen."""
    globale_einst = FahrschulEinstellungen.get_solo()
    gesamt_bericht = generiere_alle()
    messages.success(
        request,
        f"Planungshorizont auf {globale_einst.horizont_wochen} Wochen geändert: {gesamt_bericht.als_text()}.",
    )


@mitarbeiter
@require_POST
def feed_token_neu(request):
    """Setzt das Kalender-Abo zurück – etwa wenn die Abo-URL abhandengekommen ist."""
    fahrlehrer, _ = _gewaehlter_fahrlehrer(request, auch_inaktive=True)
    if fahrlehrer is None:
        messages.error(request, "Kein Fahrlehrer ausgewählt.")
        return redirect("termine:einstellungen")

    fahrlehrer.feed_token = neuer_token()
    fahrlehrer.save(update_fields=["feed_token"])
    messages.warning(
        request,
        "Neue Abo-URL erzeugt. Das alte Abo liefert ab sofort nichts mehr – "
        "bitte in allen Kalenderprogrammen austauschen.",
    )
    return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}#tab-profil")


@mitarbeiter
@require_POST
def sperrzeit_loeschen(request, pk: int):
    """Hebt eine Sperrzeit wieder auf.

    Die freien Termine, die beim Eintragen entfernt wurden, kommen dadurch
    nicht von selbst zurück – der Generator holt sie beim nächsten Lauf, und
    „Jetzt vorausplanen" tut es sofort. Das steht so auch im Hinweis.
    """
    sperre = get_object_or_404(
        Sperrzeit, pk=pk, fahrlehrer__in=_erlaubte_fahrlehrer(request.user, auch_inaktive=True)
    )
    if sperre.fsm_id:
        from .services import fsm_sync

        fsm_ids = [fid.strip() for fid in sperre.fsm_id.split(",") if fid.strip()]
        transaction.on_commit(lambda: fsm_sync.async_loesche_fsm_termine(fsm_ids))

    sperre.delete()
    messages.success(
        request,
        "Sperrzeit aufgehoben. Termine in diesem Zeitraum entstehen beim nächsten "
        "Planungslauf neu – über „Jetzt vorausplanen“ sofort.",
    )
    # Sperrzeiten stehen an zwei Stellen: in der Tagesplanung neben der Woche
    # und in den Einstellungen als Liste. Zurück geht es dorthin, wo geklickt
    # wurde – der Umweg über die jeweils andere Seite kostete nur die Ansicht.
    return redirect(_sicheres_ziel(request, "termine:einstellungen"))


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


@mitarbeiter
def fsm_einstellungen(request):
    """Verwaltung der Verknüpfungen zwischen lokalen Fahrlehrern und dem Fahrschulmanager (FSM)."""
    if not getattr(settings, "FSM_SYNC_ENABLED", False):
        raise Http404("FSM-Integration ist in dieser Installation nicht aktiviert.")

    ist_inhaber = request.user.is_staff
    fahrlehrer_liste = Fahrlehrer.objects.all().order_by("reihenfolge", "name")

    fsm_client = FsmClient()
    fsm_lehrer_liste = []
    fsm_fehler = None

    from django.core.cache import cache

    cache_key = "fsm_lehrer_liste"
    fsm_lehrer_liste = cache.get(cache_key)
    if fsm_lehrer_liste is None:
        try:
            fsm_lehrer_liste = fsm_client.get_fahrlehrer() or []
            try:
                cache.set(cache_key, fsm_lehrer_liste, timeout=300)
            except Exception:
                pass
        except FsmError as exc:
            fsm_fehler = str(exc)
            fsm_lehrer_liste = []

    # Namen einheitlich als voller_name und name bereitstellen
    for fl_item in fsm_lehrer_liste:
        if isinstance(fl_item, dict):
            if "voller_name" not in fl_item:
                v = str(fl_item.get("vorname") or "").strip()
                n = str(fl_item.get("nachname") or "").strip()
                fl_item["voller_name"] = f"{v} {n}".strip() or str(fl_item.get("displayName") or fl_item.get("name") or "Unbekannt")
            if "name" not in fl_item:
                fl_item["name"] = fl_item["voller_name"]

    if request.method == "POST":
        aktion = request.POST.get("aktion")
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", "")

        if aktion == "sync":
            from .services.fsm_sync import sync_alle_fahrlehrer

            try:
                cache.delete(cache_key)
                ergebnisse = sync_alle_fahrlehrer(client=fsm_client)
                gesamt = sum(ergebnisse.values())
                nachricht = f"Synchronisation erfolgreich: {gesamt} Sperrzeiten für {len(ergebnisse)} Fahrlehrer abgeglichen."
                if is_ajax:
                    return JsonResponse({"ok": True, "nachricht": nachricht, "gesamt": gesamt})
                messages.success(request, nachricht)
            except Exception as exc:
                if is_ajax:
                    return JsonResponse({"ok": False, "fehler": str(exc)}, status=500)
                messages.error(request, f"Fehler bei Synchronisation: {exc}")
            return redirect("termine:fsm_einstellungen")

        if aktion == "import_fahrlehrer":
            from .services.fsm_sync import importiere_fahrlehrer_aus_fsm, sync_alle_fahrlehrer

            try:
                cache.delete(cache_key)
                neu, aktualisiert = importiere_fahrlehrer_aus_fsm(client=fsm_client)
                sync_alle_fahrlehrer(client=fsm_client)
                nachricht = f"Fahrlehrer-Import erfolgreich: {len(neu)} neu angelegt, {len(aktualisiert)} verknüpft/aktualisiert."
                if is_ajax:
                    return JsonResponse({
                        "ok": True,
                        "nachricht": nachricht,
                        "neu_count": len(neu),
                        "aktualisiert_count": len(aktualisiert),
                    })
                messages.success(request, nachricht)
            except Exception as exc:
                if is_ajax:
                    return JsonResponse({"ok": False, "fehler": str(exc)}, status=500)
                messages.error(request, f"Fehler beim Import: {exc}")
            return redirect("termine:fsm_einstellungen")

        if "fsm_auth_token" in request.POST:
            token_neu = request.POST.get("fsm_auth_token", "").strip()
            if token_neu:
                fsm_client.set_auth_token(token_neu)

        # Globale FSM-Optionen speichern
        globale_einst = FahrschulEinstellungen.get_solo()
        globale_einst.fsm_theorie_blockiert_beratung = "fsm_theorie_blockiert_beratung" in request.POST
        if "fsm_sync_intervall_minuten" in request.POST:
            try:
                globale_einst.fsm_sync_intervall_minuten = int(request.POST.get("fsm_sync_intervall_minuten", 15))
            except ValueError:
                globale_einst.fsm_sync_intervall_minuten = 15
        globale_einst.save(update_fields=["fsm_theorie_blockiert_beratung", "fsm_sync_intervall_minuten"])

        from .services.fsm_sync import aktualisiere_fsm_schedule
        aktualisiere_fsm_schedule(globale_einst.fsm_sync_intervall_minuten)

        # Zuordnungen speichern
        for fahrlehrer in fahrlehrer_liste:
            key_id = f"fsm_id_{fahrlehrer.pk}"
            key_aktiv = f"fsm_sync_aktiv_{fahrlehrer.pk}"

            if key_id in request.POST:
                fahrlehrer.fsm_id = request.POST.get(key_id, "").strip()
                fahrlehrer.fsm_sync_aktiv = key_aktiv in request.POST
                fahrlehrer.save(update_fields=["fsm_id", "fsm_sync_aktiv"])

        if "sync_nach_speichern" in request.POST or request.POST.get("sync_nach_speichern") == "1":
            from .services.fsm_sync import sync_alle_fahrlehrer

            ergebnisse = sync_alle_fahrlehrer(client=fsm_client)
            gesamt = sum(ergebnisse.values())
            nachricht = f"Einstellungen gespeichert & {gesamt} Sperrzeiten synchronisiert."
            if is_ajax:
                return JsonResponse({"ok": True, "nachricht": nachricht, "gesamt": gesamt})
            messages.success(request, nachricht)
            return redirect("termine:fsm_einstellungen")

        if is_ajax:
            intervall_txt = f"alle {globale_einst.fsm_sync_intervall_minuten} Minuten" if globale_einst.fsm_sync_intervall_minuten > 0 else "deaktiviert (nur manuell)"
            return JsonResponse({
                "ok": True,
                "nachricht": "Einstellungen automatisch gespeichert.",
                "intervall": globale_einst.fsm_sync_intervall_minuten,
                "intervall_text": intervall_txt,
            })

        messages.success(request, "FSM-Einstellungen gespeichert.")
        return redirect("termine:fsm_einstellungen")

    jetzt = timezone.now()
    fahrlehrer_daten = []
    for fl in fahrlehrer_liste:
        anzahl_sperren = (
            Sperrzeit.objects.filter(fahrlehrer=fl, beginn__gte=jetzt)
            .exclude(fsm_id="")
            .count()
        )
        fahrlehrer_daten.append(
            {
                "fahrlehrer": fl,
                "fsm_sperren_count": anzahl_sperren,
            }
        )

    return render(
        request,
        "staff/fsm_einstellungen.html",
        {
            "fahrlehrer_daten": fahrlehrer_daten,
            "fsm_lehrer_liste": fsm_lehrer_liste,
            "fsm_fehler": fsm_fehler,
            "fsm_auth_token": fsm_client.get_auth_token() or "",
            "globale_einst": FahrschulEinstellungen.get_solo(),
            "ist_inhaber": ist_inhaber,
        },
    )
