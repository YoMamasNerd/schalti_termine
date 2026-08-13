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

from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

LOKALE_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}
BEISPIEL_DOMAENEN = ("example.org", "example.com", "example.net", "beispiel.de")


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
