"""Markiert den Navigationspunkt, der zur gerade offenen Seite gehört.

Ein Bereich besteht aus mehreren Seiten: Zur Tagesplanung gehören auch das
Anlegen von Terminen und das Sperren von Zeiten. Welche Adressen zu welchem
Punkt zählen, steht deshalb direkt an der Navigation in der Vorlage – dort ist
es beim Lesen sichtbar, statt in den Views verteilt zu sein.
"""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def aktiv(context, *url_namen: str) -> str:
    """Gibt "aktiv" zurück, wenn die aktuelle Adresse zu diesem Punkt gehört."""
    treffer = getattr(context.get("request"), "resolver_match", None)
    if treffer is None:
        return ""
    return "aktiv" if treffer.url_name in url_namen else ""
