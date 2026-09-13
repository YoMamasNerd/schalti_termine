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


def fsm_synchronisieren() -> str:
    """Regelmäßig: Belegungszeiten (Sperren) aus dem Fahrschulmanager abgleichen."""
    from .services.fsm_sync import sync_alle_fahrlehrer

    ergebnisse = sync_alle_fahrlehrer()
    return f"FSM-Sync: {sum(ergebnisse.values())} Sperrzeiten für {len(ergebnisse)} Fahrlehrer abgeglichen"


def system_logs_bereinigen() -> str:
    """Täglich: Logs älter als 30 Tage löschen und Tabelle deckeln."""
    from .services.logging import cleanup_old_logs

    res = cleanup_old_logs(max_days=30, max_records=10000)
    return f"Logs bereinigt: {res['gesamt_geloescht']} gelöscht ({res['verbleibend']} verbleibend)"
