"""Deutsche Feiertage pro Bundesland.

Kapselt die `holidays`-Bibliothek, damit der Rest der App nichts über deren API
wissen muss. Alles läuft offline, es wird kein externer Dienst angefragt.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

import holidays

#: Die 16 Bundesländer mit den Kürzeln, die `holidays` erwartet.
BUNDESLAENDER: tuple[tuple[str, str], ...] = (
    ("BW", "Baden-Württemberg"),
    ("BY", "Bayern"),
    ("BE", "Berlin"),
    ("BB", "Brandenburg"),
    ("HB", "Bremen"),
    ("HH", "Hamburg"),
    ("HE", "Hessen"),
    ("MV", "Mecklenburg-Vorpommern"),
    ("NI", "Niedersachsen"),
    ("NW", "Nordrhein-Westfalen"),
    ("RP", "Rheinland-Pfalz"),
    ("SL", "Saarland"),
    ("SN", "Sachsen"),
    ("ST", "Sachsen-Anhalt"),
    ("SH", "Schleswig-Holstein"),
    ("TH", "Thüringen"),
)

BUNDESLAND_CODES = frozenset(code for code, _ in BUNDESLAENDER)


@lru_cache(maxsize=256)
def _feiertage_im_jahr(bundesland: str, jahr: int) -> tuple[tuple[dt.date, str], ...]:
    """Feiertage eines Bundeslandes in einem Jahr, als unveränderliches Tupel.

    Das Ergebnis wird gecacht: Feiertage ändern sich innerhalb eines Prozesses
    nicht, und der Generator fragt dieselben Jahre sehr oft ab.
    """
    if bundesland not in BUNDESLAND_CODES:
        raise ValueError(f"Unbekanntes Bundesland: {bundesland!r}")
    kalender = holidays.Germany(subdiv=bundesland, years=[jahr], language="de")
    return tuple(sorted(kalender.items()))


def feiertag_name(bundesland: str, tag: dt.date) -> str | None:
    """Name des Feiertags oder None, wenn der Tag kein Feiertag ist."""
    for feiertag, name in _feiertage_im_jahr(bundesland, tag.year):
        if feiertag == tag:
            return name
    return None


def ist_feiertag(bundesland: str, tag: dt.date) -> bool:
    return feiertag_name(bundesland, tag) is not None


def feiertage_im_zeitraum(
    bundesland: str, von: dt.date, bis: dt.date
) -> dict[dt.date, str]:
    """Alle Feiertage zwischen `von` und `bis` (beide inklusive)."""
    ergebnis: dict[dt.date, str] = {}
    for jahr in range(von.year, bis.year + 1):
        for tag, name in _feiertage_im_jahr(bundesland, jahr):
            if von <= tag <= bis:
                ergebnis[tag] = name
    return ergebnis
