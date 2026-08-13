"""Macht den Einrichtungshinweis für Seiten verfügbar, die keine eigene View haben.

Zwei der betroffenen Seiten gehören nicht diesem Projekt: die Anmeldung des
Django-Admins und Djangos LoginView. Ihnen Kontext unterzuschieben hieße, sie
zu ersetzen. Über diesen Tag holt sich stattdessen die Vorlage, was sie
braucht – und nur dort, wo sie ihn einbindet, entsteht die Abfrage.
"""

from django import template

from ..services.einrichtung import einrichtungshinweis

register = template.Library()

register.simple_tag(einrichtungshinweis)
