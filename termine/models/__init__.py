"""Datenmodelle für Beratungstermine einer Fahrschule.

Modular aufgeteilt in:
- `common`: Gemeinsame Konstanten (Wochentage, Führerscheinklassen, Token-Generierung)
- `einstellungen`: FahrschulEinstellungen, Führerscheinklasse
- `fahrlehrer`: Fahrlehrer
- `terminart`: Terminart
- `planung`: RhythmusRegel, Sperrzeit, SperrzeitTyp
- `termin`: Termin, TerminQuerySet
- `buchung`: Buchung
"""

from __future__ import annotations

from .buchung import Buchung
from .common import (
    FUEHRERSCHEINKLASSEN,
    WOCHENTAG_KURZ,
    WOCHENTAGE,
    neuer_token,
)
from .einstellungen import FahrschulEinstellungen, Fuehrerscheinklasse
from .fahrlehrer import Fahrlehrer
from .planung import RhythmusRegel, Sperrzeit, SperrzeitTyp
from .termin import Termin, TerminQuerySet
from .terminart import Terminart

__all__ = [
    "FUEHRERSCHEINKLASSEN",
    "WOCHENTAGE",
    "WOCHENTAG_KURZ",
    "Buchung",
    "Fahrlehrer",
    "FahrschulEinstellungen",
    "Fuehrerscheinklasse",
    "RhythmusRegel",
    "Sperrzeit",
    "SperrzeitTyp",
    "Termin",
    "TerminQuerySet",
    "Terminart",
    "neuer_token",
]
