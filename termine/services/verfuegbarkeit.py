"""Abfragen für die öffentliche Buchungsseite."""

from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from ..models import Fahrlehrer, Termin, Terminart
from .feiertage import feiertage_im_zeitraum
from .planung import lokal


def freie_termine(
    *,
    fahrlehrer: Fahrlehrer | None = None,
    terminart: Terminart | None = None,
    von: dt.date | None = None,
    bis: dt.date | None = None,
) -> QuerySet[Termin]:
    """Alle buchbaren Termine, gefiltert nach Fahrlehrer, Terminart und Zeitraum."""
    qs = Termin.objects.buchbar().select_related("fahrlehrer", "terminart")
    if fahrlehrer is not None:
        qs = qs.filter(fahrlehrer=fahrlehrer)
    else:
        qs = qs.filter(fahrlehrer__aktiv=True)
    if terminart is not None:
        qs = qs.filter(terminart=terminart)
    if von is not None:
        qs = qs.filter(beginn__gte=lokal(von, dt.time.min))
    if bis is not None:
        qs = qs.filter(beginn__lte=lokal(bis, dt.time.max))
    return qs.order_by("beginn")


def termine_nach_tag(termine: QuerySet[Termin]) -> dict[dt.date, list[Termin]]:
    """Gruppiert Termine nach lokalem Kalendertag."""
    gruppen: dict[dt.date, list[Termin]] = defaultdict(list)
    for termin in termine:
        gruppen[timezone.localtime(termin.beginn).date()].append(termin)
    return dict(gruppen)


def buchungshorizont() -> dt.date:
    """Der letzte Tag, an dem freie Termine zur Buchung angeboten werden."""
    from ..models import FahrschulEinstellungen

    heute = timezone.localdate()
    wochen = (
        FahrschulEinstellungen.get_solo().horizont_wochen
        or settings.DEFAULT_HORIZON_WEEKS
    )
    return heute + dt.timedelta(days=wochen * 7 - 1)


def monatsgitter(
    jahr: int,
    monat: int,
    *,
    belegte_tage: dict[dt.date, list[Termin]],
    fahrlehrer: Fahrlehrer | None = None,
    ausgewaehlt: dt.date | None = None,
) -> list[list[dict]]:
    """Baut ein Wochen-für-Wochen-Gitter für die Monatsansicht.

    Jede Zelle enthält alles, was das Template zum Rendern braucht – so bleibt
    das Template frei von Logik.
    """
    heute = timezone.localdate()
    letzter_tag = buchungshorizont()

    kal = calendar.Calendar(firstweekday=0)  # Montag zuerst
    wochen = kal.monthdatescalendar(jahr, monat)

    erster = wochen[0][0]
    letzter = wochen[-1][-1]
    feiertage = (
        feiertage_im_zeitraum(fahrlehrer.bundesland, erster, letzter) if fahrlehrer else {}
    )

    gitter: list[list[dict]] = []
    for woche in wochen:
        zeile = []
        for tag in woche:
            termine = belegte_tage.get(tag, [])
            zeile.append(
                {
                    "datum": tag,
                    "tag": tag.day,
                    "im_monat": tag.month == monat,
                    "ist_heute": tag == heute,
                    "ist_vergangen": tag < heute,
                    "ausserhalb_horizont": tag > letzter_tag,
                    "feiertag": feiertage.get(tag),
                    "anzahl": len(termine),
                    "verfuegbar": bool(termine),
                    "ausgewaehlt": tag == ausgewaehlt,
                }
            )
        gitter.append(zeile)
    return gitter
