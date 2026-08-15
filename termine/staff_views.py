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

from .forms import (
    FahrlehrerEinstellungenForm,
    RhythmusRegelForm,
    SperrzeitForm,
    TagesplanungForm,
    TerminartForm,
)
from .models import (
    WOCHENTAG_KURZ,
    Buchung,
    Fahrlehrer,
    RhythmusRegel,
    Sperrzeit,
    Termin,
    Terminart,
    neuer_token,
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
        return redirect("termine:fahrlehrer_neu")

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

    if request.method == "POST":
        alter_horizont = fahrlehrer.horizont_wochen
        form = FahrlehrerEinstellungenForm(
            request.POST, instance=fahrlehrer, inhaber=ist_inhaber
        )
        if form.is_valid():
            fahrlehrer = form.save()
            messages.success(request, "Einstellungen gespeichert.")
            if fahrlehrer.horizont_wochen != alter_horizont:
                _horizont_nachziehen(request, fahrlehrer, alter_horizont)
            return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}")
    else:
        form = FahrlehrerEinstellungenForm(instance=fahrlehrer, inhaber=ist_inhaber)

    jetzt = timezone.now()
    return render(
        request,
        "staff/einstellungen.html",
        {
            "fahrlehrer": fahrlehrer,
            "alle_fahrlehrer": erlaubt,
            "ist_inhaber": ist_inhaber,
            "form": form,
            "buchbar_bis": timezone.localtime(fahrlehrer.spaetester_start()).date(),
            "sperrzeiten": Sperrzeit.objects.filter(
                fahrlehrer=fahrlehrer, ende__gte=jetzt
            ).order_by("beginn"),
            "terminarten": Terminart.objects.all(),
        },
    )


def _horizont_nachziehen(request, fahrlehrer: Fahrlehrer, alter_horizont: int) -> None:
    """Nach einer Änderung des Horizonts die Termine anpassen.

    Beim Verlängern erzeugt der Generator die fehlenden Wochen sofort – sonst
    stünde die neue Einstellung bis zum nächtlichen Lauf ohne Wirkung da.

    Beim Verkürzen bleiben die schon erzeugten Termine dahinter stehen: Der
    Generator räumt nur innerhalb seines Fensters auf, und ausgerechnet die
    Termine, die er nicht mehr sieht, ungefragt wegzuräumen, träfe auch die
    von Hand angelegten. Angeboten werden sie trotzdem nicht mehr – darum
    sagt der Hinweis, wo sie noch liegen.
    """
    bericht = generiere_termine(fahrlehrer)
    messages.success(
        request,
        f"Planungshorizont auf {fahrlehrer.horizont_wochen} Wochen geändert "
        f"(bis {date_format(bericht.bis, 'j. F Y')}): {bericht.als_text()}.",
    )
    if fahrlehrer.horizont_wochen >= alter_horizont:
        return
    dahinter = Termin.objects.filter(
        fahrlehrer=fahrlehrer,
        status=Termin.Status.FREI,
        beginn__gt=fahrlehrer.spaetester_start(),
    ).count()
    if dahinter:
        messages.info(
            request,
            f"{dahinter} bereits geplante Termine liegen hinter dem neuen Horizont. "
            "Kunden bekommen sie nicht mehr angeboten; in der Tagesplanung stehen "
            "sie weiterhin und lassen sich dort einzeln entfernen.",
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
    return redirect(f"{reverse('termine:einstellungen')}?fahrlehrer={fahrlehrer.slug}")


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
