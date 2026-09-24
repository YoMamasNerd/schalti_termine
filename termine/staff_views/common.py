"""Gemeinsame Grundlage des internen Bereichs: Zugangsstufen und Helfer."""

from __future__ import annotations

import calendar
import datetime as dt
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from ..models import (
    Fahrlehrer,
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

    # Standardmäßig den dem Benutzer zugeordneten Fahrlehrer wählen:
    if hasattr(request.user, "fahrlehrer") and request.user.fahrlehrer:
        if request.user.fahrlehrer in erlaubt:
            return request.user.fahrlehrer, erlaubt

    # Fallback: Suche nach E-Mail des Benutzers
    if request.user.email:
        user_fl = erlaubt.filter(email__iexact=request.user.email).first()
        if user_fl:
            return user_fl, erlaubt

    # Fallback: Suche nach Name des Benutzers
    u_first = (request.user.first_name or "").strip()
    u_last = (request.user.last_name or "").strip()
    if u_first and u_last:
        name_fl = erlaubt.filter(name__icontains=u_first).filter(name__icontains=u_last).first()
        if name_fl:
            return name_fl, erlaubt
    elif u_first:
        name_fl = erlaubt.filter(name__icontains=u_first).first()
        if name_fl:
            return name_fl, erlaubt

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
