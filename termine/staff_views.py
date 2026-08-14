"""Interner Bereich für Fahrlehrer: Tagesplanung, Regeln, Buchungen.

Der Django-Admin bleibt für Stammdaten zuständig. Diese Seiten sind für das,
was täglich gebraucht wird und im Admin zu umständlich wäre.
"""

from __future__ import annotations

import datetime as dt
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import RhythmusRegelForm, SperrzeitForm, TagesplanungForm
from .models import (
    WOCHENTAG_KURZ,
    Buchung,
    Fahrlehrer,
    RhythmusRegel,
    Sperrzeit,
    Termin,
    Terminart,
)
from .services import buchung as buchungs_service
from .services.feiertage import feiertage_im_zeitraum
from .services.planung import (
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


def _erlaubte_fahrlehrer(user):
    """Staff sieht alle, ein Fahrlehrer nur sich selbst."""
    if user.is_staff:
        return Fahrlehrer.objects.filter(aktiv=True)
    return Fahrlehrer.objects.filter(pk=user.fahrlehrer.pk)


def _gewaehlter_fahrlehrer(request):
    erlaubt = _erlaubte_fahrlehrer(request.user)
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


@mitarbeiter
def dashboard(request):
    erlaubt = _erlaubte_fahrlehrer(request.user)
    jetzt = timezone.now()

    naechste = (
        Buchung.objects.filter(
            status=Buchung.Status.BESTAETIGT,
            termin__fahrlehrer__in=erlaubt,
            termin__beginn__gte=jetzt,
        )
        .select_related("termin", "termin__terminart", "termin__fahrlehrer")
        .order_by("termin__beginn")[:10]
    )

    offen = Buchung.objects.filter(
        status=Buchung.Status.OFFEN, termin__fahrlehrer__in=erlaubt
    ).count()

    freie_pro_fahrlehrer = (
        Fahrlehrer.objects.filter(pk__in=erlaubt)
        .annotate(
            frei=Count(
                "termine",
                filter=Q(termine__status=Termin.Status.FREI, termine__beginn__gte=jetzt),
            ),
            gebucht=Count(
                "termine",
                filter=Q(termine__status=Termin.Status.GEBUCHT, termine__beginn__gte=jetzt),
            ),
        )
        .order_by("reihenfolge", "name")
    )

    return render(
        request,
        "staff/dashboard.html",
        {
            "naechste_buchungen": naechste,
            "offene_buchungen": offen,
            "fahrlehrer_liste": freie_pro_fahrlehrer,
        },
    )


@mitarbeiter
def tagesplanung(request):
    """Wochenansicht mit der Möglichkeit, jeden Tag einzeln zu planen."""
    fahrlehrer, erlaubt = _gewaehlter_fahrlehrer(request)
    if fahrlehrer is None:
        messages.warning(request, "Bitte legen Sie zuerst einen Fahrlehrer an.")
        return redirect("admin:termine_fahrlehrer_add")

    roh_woche = request.GET.get("woche")
    try:
        bezug = dt.date.fromisoformat(roh_woche) if roh_woche else timezone.localdate()
    except ValueError:
        bezug = timezone.localdate()
    montag = _montag(bezug)
    sonntag = montag + dt.timedelta(days=6)

    termine = (
        Termin.objects.filter(
            fahrlehrer=fahrlehrer,
            beginn__gte=lokal(montag, dt.time.min),
            beginn__lte=lokal(sonntag, dt.time.max),
        )
        # Entfallene Termine stehen nur noch als Beleg in der Datenbank; in der
        # Wochenansicht wären sie eine Zeile, an der es nichts zu tun gibt.
        .exclude(status=Termin.Status.ENTFALLEN)
        .select_related("terminart")
        .prefetch_related("buchungen")
        .order_by("beginn")
    )

    nach_tag: dict[dt.date, list[Termin]] = {
        montag + dt.timedelta(days=i): [] for i in range(7)
    }
    for termin in termine:
        nach_tag.setdefault(termin.tag, []).append(termin)

    feiertage = feiertage_im_zeitraum(fahrlehrer.bundesland, montag, sonntag)
    sperren = Sperrzeit.objects.filter(
        fahrlehrer=fahrlehrer,
        beginn__lt=lokal(sonntag, dt.time.max),
        ende__gt=lokal(montag, dt.time.min),
    )

    heute = timezone.localdate()
    tage = []
    for i in range(7):
        tag = montag + dt.timedelta(days=i)
        tages_termine = nach_tag.get(tag, [])
        tage.append(
            {
                "datum": tag,
                "kurz": WOCHENTAG_KURZ[tag.weekday()],
                "label": date_format(tag, "j. F"),
                "ist_heute": tag == heute,
                "ist_vergangen": tag < heute,
                "feiertag": feiertage.get(tag),
                "termine": tages_termine,
                "frei": sum(1 for t in tages_termine if t.status == Termin.Status.FREI),
                "gebucht": sum(
                    1
                    for t in tages_termine
                    if t.status in (Termin.Status.GEBUCHT, Termin.Status.RESERVIERT)
                ),
            }
        )

    planungs_form = TagesplanungForm(
        initial={"fahrlehrer": fahrlehrer, "tag": max(heute, montag)}
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
            "montag": montag,
            "sonntag": sonntag,
            "vorherige_woche": montag - dt.timedelta(days=7),
            "naechste_woche": montag + dt.timedelta(days=7),
            "diese_woche": _montag(heute),
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
@require_POST
def buchung_absagen(request, pk: int):
    buchung = get_object_or_404(
        Buchung, pk=pk, termin__fahrlehrer__in=_erlaubte_fahrlehrer(request.user)
    )
    if not buchung.ist_aktiv:
        messages.info(request, "Diese Buchung war bereits beendet.")
    else:
        buchungs_service.stornieren(buchung, von="fahrschule")
        messages.success(request, f"Buchung von {buchung.name} abgesagt, der Kunde wurde informiert.")
    return redirect("termine:buchungen")


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
