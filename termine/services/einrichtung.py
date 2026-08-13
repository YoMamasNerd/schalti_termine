"""Erkennt eine Installation, in der noch niemand die Stammdaten pflegen kann.

Nach `migrate` läuft die App technisch einwandfrei – und ist trotzdem für
niemanden zu gebrauchen: Ohne Superuser kommt man nicht in den Django-Admin,
ohne Admin gibt es weder Terminart noch Fahrlehrer, und ohne die beiden gibt
es nichts zu buchen. Der Kalender ist dann einfach leer, die Anmeldeseite
weist jedes Passwort ab. Beides sieht aus wie ein Fehler, ist aber nur ein
fehlender erster Schritt.

Die Prüfung fragt deshalb nach dem Superuser, nicht nach irgendeinem Konto:
Ein Fahrlehrer-Login kann die eigene Planung pflegen, aber keine Stammdaten
anlegen. Solange niemand das darf, steht die Installation still.
"""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import DatabaseError

# Docker legt diese Datei in jedem Container an. Sie entscheidet nur darüber,
# welche Variante des Befehls im Hinweis steht – ein Fehlgriff wäre also
# unschön, aber harmlos.
CONTAINER_MERKMAL = Path("/.dockerenv")

COMPOSE_DIENST = "web"


def superuser_fehlt() -> bool:
    """True, wenn kein Superuser existiert.

    Ein Datenbankfehler (etwa vor der ersten Migration) zählt bewusst als
    „kein Anlass für einen Hinweis". Diese Funktion läuft beim Rendern von
    Seiten; sie darf eine Seite niemals zum Absturz bringen, nur weil die
    Auskunft gerade nicht zu holen ist.
    """
    try:
        return not get_user_model().objects.filter(is_superuser=True).exists()
    except DatabaseError:
        return False


def createsuperuser_befehl() -> str:
    """Der Befehl in der Form, die auf dieser Installation tatsächlich passt."""
    if CONTAINER_MERKMAL.exists():
        return f"docker compose exec {COMPOSE_DIENST} python manage.py createsuperuser"
    return "python manage.py createsuperuser"


def einrichtungshinweis() -> dict | None:
    """Kontext für den Hinweis – oder None, wenn alles eingerichtet ist."""
    if not superuser_fehlt():
        return None
    return {"befehl": createsuperuser_befehl()}
