"""Einstiegspunkte für die Hintergrundjobs (django-q2).

Diese Funktionen werden per Pfad-String vom Scheduler aufgerufen und müssen
daher auf Modulebene liegen und ohne Argumente laufen.
"""

from __future__ import annotations

from .services.buchung import (
    abgelaufene_reservierungen_freigeben,
    alte_buchungen_anonymisieren,
    erinnerungen_versenden,
)
from .services.planung import generiere_alle


def termine_vorausplanen() -> str:
    """Täglich: den Planungshorizont aller Fahrlehrer weiterschieben."""
    bericht = generiere_alle()
    return bericht.als_text()


def reservierungen_aufraeumen() -> str:
    """Alle paar Minuten: nicht bestätigte Reservierungen wieder freigeben."""
    return f"{abgelaufene_reservierungen_freigeben()} Reservierungen freigegeben"


def erinnerungen() -> str:
    """Stündlich: Erinnerungsmails für anstehende Termine."""
    return f"{erinnerungen_versenden()} Erinnerungen versendet"


def datenpflege() -> str:
    """Täglich: personenbezogene Daten alter Buchungen löschen."""
    return f"{alte_buchungen_anonymisieren()} Buchungen anonymisiert"
