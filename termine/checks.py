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
GEHEIMNISSE_AUS_DER_UMGEBUNG = ("POSTGRES_PASSWORD", "EMAIL_HOST_PASSWORD")


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
    """Ohne funktionierenden Mailversand ist keine Buchung abschließbar."""
    if settings.DEBUG:
        return []

    meldungen = []

    if settings.EMAIL_BACKEND.endswith("console.EmailBackend"):
        meldungen.append(
            Error(
                "E-Mails werden auf die Konsole geschrieben statt versendet.",
                hint="Ohne Versand erhält niemand den Bestätigungslink, und keine "
                "Buchung kommt zustande. Setzen Sie EMAIL_HOST und die zugehörigen "
                "Zugangsdaten in der .env.",
                id="termine.E002",
            )
        )

    mailserver = getattr(settings, "EMAIL_HOST", "") or ""
    if any(domaene in mailserver for domaene in BEISPIEL_DOMAENEN):
        meldungen.append(
            Error(
                f"EMAIL_HOST steht noch auf einem Beispielserver ({mailserver!r}).",
                hint="Die Installation fährt damit hoch, aber der Versand scheitert bei "
                "jeder Buchung – und weil der Bestätigungslink nie ankommt, kommt keine "
                "Buchung zustande. Tragen Sie den SMTP-Server Ihres Anbieters ein.",
                id="termine.E003",
            )
        )

    absender = settings.DEFAULT_FROM_EMAIL or ""
    if any(domaene in absender for domaene in BEISPIEL_DOMAENEN):
        meldungen.append(
            Warning(
                f"DEFAULT_FROM_EMAIL steht noch auf einer Beispieladresse ({absender!r}).",
                hint="Viele Mailserver verwerfen Nachrichten mit einer Absenderadresse, "
                "deren Domain ihnen nicht gehört. Tragen Sie die echte Adresse der "
                "Fahrschule ein.",
                id="termine.W002",
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
