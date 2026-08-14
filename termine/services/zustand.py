"""Auskunft darüber, ob die Installation gerade arbeitsfähig ist.

Zwei Fragen, zwei Antworten – bewusst getrennt, weil sie verschiedene
Container betreffen:

* **Datenbank erreichbar?** Das ist die Frage an den Webserver. Antwortet
  gunicorn, aber die Datenbank ist weg, sieht jede Seite kaputt aus; ein
  Prozess-Check würde das nicht bemerken.
* **Laufen die Jobs?** Das ist die Frage an den Worker – und die interessante:
  Fällt er aus, stürzt nichts ab. Es kommen nur keine Termine mehr dazu,
  Erinnerungen bleiben aus und Reservierungen werden nicht mehr freigegeben.
  Genau die Sorte Ausfall, die tagelang niemandem auffällt.

Für die zweite Frage taugt kein Prozess-Check: `qcluster` kann laufen und
trotzdem nichts abarbeiten. Verlässlich ist der Fahrplan selbst. django-q2
schiebt `next_run` nach jeder Ausführung weiter; bleibt der Zeitpunkt in der
Vergangenheit stehen, arbeitet niemand die Warteschlange ab. Der häufigste
Job läuft alle fünf Minuten – deshalb ist eine Viertelstunde Rückstand ein
klares Zeichen und keine Zufälligkeit.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.db import DatabaseError, connection
from django.utils import timezone

TOLERANZ_MINUTEN = 15


@dataclass(frozen=True)
class Befund:
    gesund: bool
    meldung: str


def datenbank() -> Befund:
    """Fragt die Datenbank mit der billigsten möglichen Abfrage."""
    try:
        with connection.cursor() as zeiger:
            zeiger.execute("SELECT 1")
    except DatabaseError as fehler:
        return Befund(False, f"Datenbank nicht erreichbar ({type(fehler).__name__})")
    return Befund(True, "Datenbank erreichbar")


def jobs(toleranz_minuten: int = TOLERANZ_MINUTEN) -> Befund:
    """Prüft, ob der Fahrplan der Hintergrundjobs weiterläuft."""
    from django_q.models import Schedule

    grenze = timezone.now() - dt.timedelta(minutes=toleranz_minuten)
    try:
        geplant = Schedule.objects.count()
        ueberfaellig = sorted(
            Schedule.objects.filter(next_run__lt=grenze).values_list("name", flat=True)
        )
    except DatabaseError as fehler:
        return Befund(False, f"Fahrplan nicht lesbar ({type(fehler).__name__})")

    if not geplant:
        return Befund(
            False, "Kein Job eingetragen – „python manage.py jobs_einrichten“ fehlt."
        )
    if ueberfaellig:
        return Befund(
            False,
            f"Seit über {toleranz_minuten} Minuten überfällig: {', '.join(ueberfaellig)}. "
            "Läuft „python manage.py qcluster“?",
        )
    mehrzahl = "Jobs" if geplant != 1 else "Job"
    return Befund(True, f"{geplant} {mehrzahl} im Fahrplan, keiner überfällig")
