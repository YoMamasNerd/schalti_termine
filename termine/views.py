"""Öffentliche Buchungsseite – kein Login nötig."""

from __future__ import annotations

import calendar
import datetime as dt
import logging

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from .forms import BuchungsForm, StornoForm
from .models import Buchung, Fahrlehrer, Termin, Terminart
from .services import buchung as buchungs_service
from .services import zustand
from .services.feiertage import feiertag_name
from .services.ics import fahrlehrer_feed
from .services.verfuegbarkeit import (
    buchungshorizont,
    freie_termine,
    monatsgitter,
    termine_nach_tag,
)

logger = logging.getLogger(__name__)


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


def _auswahl(request, slug: str | None = None):
    """Fahrlehrer- und Terminart-Auswahl aus URL bzw. Query-Parametern."""
    fahrlehrer = None
    if slug:
        fahrlehrer = get_object_or_404(Fahrlehrer, slug=slug, aktiv=True)
    elif request.GET.get("fahrlehrer"):
        fahrlehrer = Fahrlehrer.objects.filter(
            slug=request.GET["fahrlehrer"], aktiv=True
        ).first()

    terminart = None
    if request.GET.get("art"):
        terminart = Terminart.objects.filter(slug=request.GET["art"], aktiv=True).first()
    return fahrlehrer, terminart


def _monatsgrenzen(jahr: int, monat: int) -> tuple[dt.date, dt.date]:
    """Erster und letzter im Monatsgitter sichtbarer Tag (inkl. Nachbarmonate)."""
    wochen = calendar.Calendar(firstweekday=0).monthdatescalendar(jahr, monat)
    return wochen[0][0], wochen[-1][-1]


def _basis_kontext(request, slug: str | None = None) -> dict:
    fahrlehrer, terminart = _auswahl(request, slug)
    alle_fahrlehrer = list(Fahrlehrer.objects.filter(aktiv=True))
    alle_terminarten = list(Terminart.objects.filter(aktiv=True))

    # Gibt es nur einen Fahrlehrer, hat der Kunde nichts zu entscheiden. Er wird
    # dann still verwendet – so erscheinen seine Feiertage im Kalender und sein
    # Name verschwindet aus der Terminliste, ohne dass die URL einen
    # überflüssigen Parameter mitschleppt.
    fahrlehrer_implizit = fahrlehrer is None and len(alle_fahrlehrer) == 1
    if fahrlehrer_implizit:
        fahrlehrer = alle_fahrlehrer[0]

    jahr, monat = _monat_aus_get(request)
    try:
        gitter_von, gitter_bis = _monatsgrenzen(jahr, monat)
    except (calendar.IllegalMonthError, ValueError):
        heute = timezone.localdate()
        jahr, monat = heute.year, heute.month
        gitter_von, gitter_bis = _monatsgrenzen(jahr, monat)

    termine = freie_termine(
        fahrlehrer=fahrlehrer, terminart=terminart, von=gitter_von, bis=gitter_bis
    )
    nach_tag = termine_nach_tag(termine)

    gewaehlter_tag = _datum_aus_get(request, "tag")
    if gewaehlter_tag is None or gewaehlter_tag not in nach_tag:
        # Ohne explizite Auswahl den nächsten Tag mit freien Terminen zeigen.
        kandidaten = sorted(nach_tag)
        gewaehlter_tag = kandidaten[0] if kandidaten else None

    tages_termine = nach_tag.get(gewaehlter_tag, []) if gewaehlter_tag else []

    heute = timezone.localdate()
    erster_im_monat = dt.date(jahr, monat, 1)
    vorheriger = erster_im_monat - dt.timedelta(days=1)
    naechster = (erster_im_monat + dt.timedelta(days=32)).replace(day=1)
    horizont = buchungshorizont()

    return {
        "fahrlehrer": fahrlehrer,
        "terminart": terminart,
        "alle_fahrlehrer": alle_fahrlehrer,
        "alle_terminarten": alle_terminarten,
        # Nur anbieten, wo es tatsächlich etwas zu wählen gibt.
        "fahrlehrer_auswahl": len(alle_fahrlehrer) > 1,
        "terminart_auswahl": len(alle_terminarten) > 1,
        "jahr": jahr,
        "monat": monat,
        "monatsname": date_format(erster_im_monat, "F Y"),
        "gitter": monatsgitter(
            jahr, monat, belegte_tage=nach_tag, fahrlehrer=fahrlehrer, ausgewaehlt=gewaehlter_tag
        ),
        "vorheriger_monat": None if vorheriger < heute.replace(day=1) else f"{vorheriger:%Y-%m}",
        "naechster_monat": None if naechster > horizont else f"{naechster:%Y-%m}",
        "gewaehlter_tag": gewaehlter_tag,
        "tages_termine": tages_termine,
        "tage_gesamt": sum(len(v) for v in nach_tag.values()),
        "feiertag": (
            feiertag_name(fahrlehrer.bundesland, gewaehlter_tag)
            if fahrlehrer and gewaehlter_tag
            else None
        ),
        "horizont": horizont,
        "querystring": _querystring(None if fahrlehrer_implizit else fahrlehrer, terminart),
    }


def _querystring(fahrlehrer, terminart) -> str:
    teile = []
    if fahrlehrer:
        teile.append(f"fahrlehrer={fahrlehrer.slug}")
    if terminart:
        teile.append(f"art={terminart.slug}")
    return "&".join(teile)


def startseite(request, slug: str | None = None):
    return render(request, "termine/start.html", _basis_kontext(request, slug))


def einbetten(request):
    """Nur die Terminauswahl – gedacht für einen Rahmen in einer fremden Seite.

    Wer einen Rahmen darum setzen darf, steht in EMBED_ORIGINS. Ohne Eintrag
    ist die Seite weiterhin direkt aufrufbar, aber von niemandem
    einbettbar – ein offener Rahmen lädt zum Klickfang ein, bei dem eine
    fremde Seite die Auswahl unsichtbar über eigene Schaltflächen legt.

    Umgesetzt über frame-ancestors und nicht über X-Frame-Options: Letzteres
    kennt nur eine einzige Adresse, und die Fahrschule hat oft zwei (mit und
    ohne www).
    """
    kontext = _basis_kontext(request)
    kontext["einbetten"] = True
    antwort = render(request, "termine/einbetten.html", kontext)

    erlaubt = settings.EMBED_ORIGINS
    if erlaubt:
        antwort.headers["Content-Security-Policy"] = "frame-ancestors " + " ".join(erlaubt)
        # Sagt der XFrameOptionsMiddleware, dass sie ihr DENY hier weglässt;
        # sonst überstimmt es die Freigabe oben.
        antwort.xframe_options_exempt = True
    else:
        antwort.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    return antwort


def buchen(request, termin_id: int):
    termin = get_object_or_404(
        Termin.objects.select_related("fahrlehrer", "terminart"), pk=termin_id
    )

    if termin.status != Termin.Status.FREI or termin.beginn < termin.fahrlehrer.fruehester_start():
        return render(
            request,
            "termine/nicht_verfuegbar.html",
            {"termin": termin},
            status=409,
        )

    if request.method == "POST":
        form = BuchungsForm(request.POST, terminart=termin.terminart)
        if form.is_valid():
            daten = form.cleaned_data
            try:
                buchung = buchungs_service.reservieren(
                    termin.pk,
                    name=daten["name"],
                    email=daten["email"],
                    telefon=daten.get("telefon", ""),
                    fuehrerscheinklasse=daten.get("fuehrerscheinklasse", ""),
                    nachricht=daten.get("nachricht", ""),
                )
            except buchungs_service.TerminNichtVerfuegbar as fehler:
                return render(
                    request,
                    "termine/nicht_verfuegbar.html",
                    {"termin": termin, "meldung": str(fehler)},
                    status=409,
                )
            return redirect("termine:buchung", token=buchung.token)
    else:
        form = BuchungsForm(terminart=termin.terminart)

    return render(request, "termine/buchen.html", {"termin": termin, "form": form})


def _buchung_holen(token: str) -> Buchung:
    buchung = (
        Buchung.objects.select_related("termin", "termin__terminart", "termin__fahrlehrer")
        .filter(token=token)
        .first()
    )
    if buchung is None:
        raise Http404("Diese Buchung gibt es nicht (mehr).")
    return buchung


def bestaetigen(request, token: str):
    """Zweiter Schritt des Double-Opt-in – verbindlich erst nach dem Knopfdruck.

    Der Aufruf des Links zeigt nur eine Seite mit einem Knopf; gebucht wird per
    POST. Das ist kein Umstand um seiner selbst willen: Viele Postfächer lassen
    eingehende Links vorab von einem Scanner abrufen (Outlook Safe Links und
    Verwandte). Bestätigte der bloße Aufruf, hätte der Scanner die Buchung
    verbindlich gemacht, ohne dass ein Mensch je geklickt hat – und genau das
    soll das Double-Opt-in ja verhindern.
    """
    buchung = _buchung_holen(token)

    if request.method != "POST":
        return render(
            request,
            "termine/bestaetigen.html",
            {
                "buchung": buchung,
                "termin": buchung.termin,
                "schon_bestaetigt": buchung.status == Buchung.Status.BESTAETIGT,
                "abgelaufen": buchung.ist_abgelaufen,
            },
        )

    fehler = None
    try:
        buchung = buchungs_service.bestaetigen(buchung)
    except buchungs_service.BuchungsFehler as exc:
        fehler = str(exc)
    return render(
        request,
        "termine/bestaetigt.html",
        {"buchung": buchung, "termin": buchung.termin, "fehler": fehler},
    )


def buchung_ansicht(request, token: str):
    buchung = _buchung_holen(token)
    return render(
        request,
        "termine/buchung.html",
        {
            "buchung": buchung,
            "termin": buchung.termin,
            "form": StornoForm(),
            "stornierbar": buchung.ist_aktiv and buchung.termin.beginn > timezone.now(),
        },
    )


@require_POST
def buchung_stornieren(request, token: str):
    buchung = _buchung_holen(token)
    if not buchung.ist_aktiv:
        messages.info(request, "Diese Buchung war bereits storniert.")
    elif buchung.termin.beginn <= timezone.now():
        messages.error(request, "Dieser Termin liegt bereits in der Vergangenheit.")
    else:
        buchungs_service.stornieren(buchung, von="kunde")
        messages.success(request, "Ihr Termin wurde storniert. Sie erhalten eine Bestätigung per E-Mail.")
    return redirect("termine:buchung", token=token)


def ics_feed(request, token: str):
    """Kalender-Abo für einen Fahrlehrer. Der Token ist das einzige Geheimnis."""
    fahrlehrer = Fahrlehrer.objects.filter(feed_token=token).first()
    if fahrlehrer is None:
        raise Http404("Unbekannter Kalender.")
    antwort = HttpResponse(fahrlehrer_feed(fahrlehrer), content_type="text/calendar; charset=utf-8")
    antwort["Content-Disposition"] = f'inline; filename="{fahrlehrer.slug}.ics"'
    antwort["Cache-Control"] = "private, max-age=300"
    antwort["X-Robots-Tag"] = "noindex, nofollow"
    return antwort


def healthz(request):
    """Zustandsprüfung für den Webserver – ohne Login, absichtlich karg.

    Die Seite ist öffentlich erreichbar und sagt deshalb nach außen nur „ok"
    oder „fehler". Woran es liegt, steht im Log; ein Fremder muss aus dieser
    Antwort nicht ablesen können, welcher Teil der Anlage gerade schwächelt.
    """
    befund = zustand.datenbank()
    if not befund.gesund:
        logger.warning("Zustandsprüfung fehlgeschlagen: %s", befund.meldung)

    antwort = HttpResponse(
        "ok\n" if befund.gesund else "fehler\n",
        status=200 if befund.gesund else 503,
        content_type="text/plain; charset=utf-8",
    )
    antwort["Cache-Control"] = "no-store"
    return antwort
