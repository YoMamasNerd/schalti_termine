"""Interner Bereich für Fahrlehrer: Tagesplanung, Regeln, Buchungen, Einstellungen.

Hier steht alles, was die Fahrschule im Betrieb braucht – einschließlich der
Stammdaten, die sie selbst pflegt. Der Zugang hat zwei Stufen und beide stehen
genau einmal in common: `mitarbeiter` lässt herein, `inhaber` schränkt auf das
ein, was für die ganze Fahrschule gilt. Welche Daten jemand dann sieht,
entscheidet allein `_erlaubte_fahrlehrer` – verteilte Prüfungen in den Views
gibt es bewusst nicht.

Die Ansichten gruppiert nach Themen: dashboard, planung, buchungen, regeln,
stammdaten, einstellungen. common hält die Zugangsstufen und Helfer. Die
Re-Exporte unten halten `urls.py` und den Sammeltest in test_zugriff.py
(`staff_views.__name__` als Modul-Marker) funktionsfähig.
"""

from .buchungen import (
    buchung_absagen,
    buchung_detail,
    buchung_verschieben,
    buchung_wieder_einbuchen,
    buchungsliste,
    historie,
)
from .common import inhaber, mitarbeiter
from .dashboard import dashboard
from .einstellungen import (
    cleanup_logs_action,
    disconnect_sso,
    einstellungen,
    feed_token_neu,
    fsm_einstellungen,
    smtp_test_ajax,
    sperrzeit_loeschen,
    system_logs,
)
from .planung import (
    generieren,
    kollision_anlegen,
    kollision_ignorier_rueckgangig,
    kollision_ignorieren,
    sperrzeit_anlegen,
    tagesplanung,
    termin_loeschen,
    termine_anlegen,
)
from .regeln import regel_bearbeiten, regel_loeschen, regelliste
from .stammdaten import (
    fahrlehrer_neu,
    klasse_bearbeiten,
    klasse_loeschen,
    klassenliste,
    terminart_bearbeiten,
    terminart_loeschen,
    terminartenliste,
)

__all__ = [
    "buchung_absagen",
    "buchung_detail",
    "buchung_verschieben",
    "buchung_wieder_einbuchen",
    "buchungsliste",
    "cleanup_logs_action",
    "dashboard",
    "disconnect_sso",
    "einstellungen",
    "fahrlehrer_neu",
    "feed_token_neu",
    "fsm_einstellungen",
    "generieren",
    "historie",
    "inhaber",
    "klasse_bearbeiten",
    "klasse_loeschen",
    "klassenliste",
    "kollision_anlegen",
    "kollision_ignorier_rueckgangig",
    "kollision_ignorieren",
    "mitarbeiter",
    "regel_bearbeiten",
    "regel_loeschen",
    "regelliste",
    "smtp_test_ajax",
    "sperrzeit_anlegen",
    "sperrzeit_loeschen",
    "system_logs",
    "tagesplanung",
    "termin_loeschen",
    "terminart_bearbeiten",
    "terminart_loeschen",
    "terminartenliste",
    "termine_anlegen",
]
