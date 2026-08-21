"""Systemprüfungen für die Terminbuchung.

Diese App baut Links außerhalb eines Requests: Bestätigungs- und Storno-Links
in E-Mails sowie die Abo-URL des Kalenders entstehen in Hintergrundjobs, wo es
keinen `request` gibt, aus dem Django den Hostnamen ableiten könnte. Sie
stammen deshalb alle aus `SITE_BASE_URL`.

Steht dort der falsche Wert, sieht im Betrieb zunächst alles normal aus – bis
der erste Interessent einen Bestätigungslink anklickt, der auf seinem eigenen
Rechner landet. Genau diese Klasse von Fehlern fangen die folgenden Prüfungen
ab.

Die beiden Konfigurationsprüfungen sind mit `deploy=True` registriert und
laufen deshalb nur bei `manage.py check --deploy`. Das ist Absicht: Sie
beurteilen die Umgebung, nicht den Code, und dürfen weder den Testlauf noch
die tägliche Entwicklung stören. Der Docker-Container ruft sie beim Start auf,
bevor migriert wird – eine falsch konfigurierte Installation fährt damit gar
nicht erst hoch, statt tote Links zu verschicken.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

LOKALE_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}
BEISPIEL_DOMAENEN = ("example.org", "example.com", "example.net", "beispiel.de")

# So sind die Felder in .env.example vorbelegt. Wer die Datei kopiert und nur
# halb ausfüllt, lässt diesen Wert stehen.
PLATZHALTER = "bitte-aendern"
ENTWICKLUNGSSCHLUESSEL = "unsicher-nur-fuer-entwicklung"

# Geheimnisse, die aus der Umgebung kommen und deshalb nicht in den Settings
# stehen, wo eine Prüfung sie sonst fände.
GEHEIMNISSE_AUS_DER_UMGEBUNG = ("POSTGRES_PASSWORD",)


@register(Tags.security, deploy=True)
def pruefe_oeffentliche_adresse(app_configs, **kwargs):
    """SITE_BASE_URL muss im Betrieb die echte, von außen erreichbare Adresse sein."""
    if settings.DEBUG:
        return []

    zerlegt = urlparse(settings.SITE_BASE_URL)
    meldungen = []

    if (zerlegt.hostname or "") in LOKALE_HOSTS:
        meldungen.append(
            Error(
                "SITE_BASE_URL zeigt auf den lokalen Rechner "
                f"({settings.SITE_BASE_URL!r}).",
                hint="Alle Bestätigungs- und Storno-Links in den E-Mails sowie die "
                "Kalender-Abo-URL werden daraus gebaut. Mit diesem Wert wären sie "
                "für Ihre Kunden nicht erreichbar. Tragen Sie in der .env die "
                "öffentliche Adresse ein, z. B. "
                "SITE_BASE_URL=https://termine.meine-fahrschule.de",
                id="termine.E001",
            )
        )
    elif zerlegt.scheme != "https":
        meldungen.append(
            Warning(
                f"SITE_BASE_URL benutzt {zerlegt.scheme or 'kein'}:// statt https "
                f"({settings.SITE_BASE_URL!r}).",
                hint="Die Links enthalten den Zugriffs-Token einer Buchung. Ohne TLS "
                "wandert dieser Token im Klartext durch das Netz.",
                id="termine.W001",
            )
        )

    return meldungen


@register(Tags.security, deploy=True)
def pruefe_mailversand(app_configs, **kwargs):
    """Prüft, ob in den Fahrschul-Einstellungen ein SMTP-Server hinterlegt ist."""
    if settings.DEBUG:
        return []

    meldungen = []
    try:
        from .models import FahrschulEinstellungen

        einst = FahrschulEinstellungen.get_solo()
        mailserver = einst.email_host or ""
    except Exception:
        return []

    if not mailserver:
        meldungen.append(
            Warning(
                "Es ist noch kein SMTP-Server in den Fahrschul-Einstellungen hinterlegt.",
                hint="Richten Sie die SMTP-Zugangsdaten in der Verwaltung unter "
                "/intern/einstellungen/ ein, damit Buchungs- und Bestätigungs-E-Mails versendet werden.",
                id="termine.W002",
            )
        )
    elif any(domaene in mailserver for domaene in BEISPIEL_DOMAENEN):
        meldungen.append(
            Error(
                f"Der SMTP-Server steht noch auf einem Beispielserver ({mailserver!r}).",
                hint="Tragen Sie in den Einstellungen unter /intern/einstellungen/ den echten SMTP-Server ein.",
                id="termine.E003",
            )
        )

    return meldungen


@register(Tags.security, deploy=True)
def pruefe_platzhalter(app_configs, **kwargs):
    """Kein Geheimnis darf so bleiben, wie es in .env.example steht.

    Djangos eigene Prüfung meckert einen zu kurzen Schlüssel nur als Warnung
    an – und Warnungen halten den Container nicht auf, der mit
    `--fail-level ERROR` startet. Ein unverändertes Geheimnis ist aber kein
    Schönheitsfehler: Wer die Beispieldatei kennt, kennt damit den Schlüssel,
    mit dem diese Installation ihre Sitzungen signiert.
    """
    if settings.DEBUG:
        return []

    meldungen = []

    schluessel = settings.SECRET_KEY or ""
    if PLATZHALTER in schluessel or schluessel == ENTWICKLUNGSSCHLUESSEL:
        meldungen.append(
            Error(
                "DJANGO_SECRET_KEY steht noch auf dem Wert aus der Beispieldatei.",
                hint="Damit sind Sitzungen und Formular-Token für jeden fälschbar, der "
                "diese Beispieldatei kennt. Neuen Wert erzeugen mit: python -c "
                "'import secrets; print(secrets.token_urlsafe(50))'",
                id="termine.E004",
            )
        )

    unveraendert = [
        name
        for name in GEHEIMNISSE_AUS_DER_UMGEBUNG
        if PLATZHALTER in os.environ.get(name, "")
    ]
    if unveraendert:
        meldungen.append(
            Error(
                f"Unverändert aus der Beispieldatei übernommen: {', '.join(unveraendert)}.",
                hint="Tragen Sie die echten Zugangsdaten in die .env ein.",
                id="termine.E005",
            )
        )

    return meldungen


@register()
def pruefe_reservierungsdauer(app_configs, **kwargs):
    """Die Reservierung muss lange genug sein, dass ein Mensch die Mail abrufen kann."""
    if settings.RESERVATION_MINUTES < 5:
        return [
            Warning(
                f"RESERVATION_MINUTES ist mit {settings.RESERVATION_MINUTES} Minuten sehr kurz.",
                hint="So schnell ruft kaum jemand seine E-Mails ab. Die Reservierung "
                "läuft dann ab, bevor der Bestätigungslink angeklickt wird, und die "
                "Buchung scheitert. Empfohlen sind 15 bis 60 Minuten.",
                id="termine.W003",
            )
        ]
    return []
