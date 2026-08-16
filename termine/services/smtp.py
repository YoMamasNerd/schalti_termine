"""Service für SMTP-Verbindungsprüfung, Live-Authentifizierungstest und Test-E-Mails."""

from __future__ import annotations

import logging
import smtplib
import socket
import ssl
from typing import NamedTuple

from django.core.mail import EmailMultiAlternatives, get_connection

logger = logging.getLogger(__name__)


class SmtpTestErgebnis(NamedTuple):
    ok: bool
    meldung: str
    details: str = ""


def teste_smtp_authentifizierung(
    host: str,
    port: int | str = 587,
    user: str = "",
    password: str = "",
    use_tls: bool = True,
    use_ssl: bool = False,
    timeout: int = 8,
) -> SmtpTestErgebnis:
    """Testet die Erreichbarkeit und Authentifizierung am SMTP-Server live.

    Prüft:
    1. DNS-Auflösung & TCP-Verbindungsaufbau
    2. Begrüßung (EHLO/HELO)
    3. TLS-Handshake (STARTTLS auf Port 587 oder SSL auf Port 465)
    4. Authentifizierung (Login mit Benutzername und Passwort)
    """
    host = (host or "").strip()
    if not host:
        return SmtpTestErgebnis(False, "Kein SMTP-Host angegeben.", "Bitte geben Sie einen Servernamen ein (z. B. smtp.strato.de).")

    try:
        port = int(port or (465 if use_ssl else 587))
    except (ValueError, TypeError):
        return SmtpTestErgebnis(False, f"Ungültige Portnummer: {port}", "Der Port muss eine Zahl sein (meist 587 oder 465).")

    user = (user or "").strip()
    password = password or ""
    server = None

    try:
        if use_ssl:
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)

        # 1. EHLO
        server.ehlo()

        # 2. STARTTLS (falls Port nicht rein SSL ist)
        if use_tls and not use_ssl:
            if not server.has_extn("starttls"):
                return SmtpTestErgebnis(
                    False,
                    f"STARTTLS wird vom Server {host}:{port} nicht unterstützt.",
                    "Deaktivieren Sie STARTTLS oder aktivieren Sie SSL/TLS (Port 465), falls der Anbieter reines SSL verlangt.",
                )
            context = ssl.create_default_context()
            server.starttls(context=context)
            server.ehlo()

        # 3. Authentifizierung prüfen
        if user:
            if not password:
                return SmtpTestErgebnis(
                    False,
                    "Benutzername angegeben, aber kein Passwort hinterlegt.",
                    "Für die Anmeldung am SMTP-Server ist ein Passwort erforderlich.",
                )
            server.login(user, password)
            verschluesselung = "SSL/TLS" if use_ssl else ("STARTTLS" if use_tls else "unverschlüsselt")
            return SmtpTestErgebnis(
                True,
                f"Verbindung und Authentifizierung für „{user}“ erfolgreich!",
                f"Verbunden mit {host}:{port} über {verschluesselung}. Der Server hat die Zugangsdaten akzeptiert.",
            )
        else:
            verschluesselung = "SSL/TLS" if use_ssl else ("STARTTLS" if use_tls else "unverschlüsselt")
            return SmtpTestErgebnis(
                True,
                f"Verbindung zu {host}:{port} erfolgreich hergestellt (ohne Login).",
                f"Verschlüsselung: {verschluesselung}. Hinweis: Es wurden keine Zugangsdaten hinterlegt.",
            )

    except smtplib.SMTPAuthenticationError as exc:
        err_text = exc.smtp_error.decode("utf-8", errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        return SmtpTestErgebnis(
            False,
            f"Authentifizierung fehlgeschlagen (Code {exc.smtp_code})",
            f"Benutzername oder Passwort wurden vom Server {host} abgelehnt: {err_text}",
        )
    except (socket.timeout, TimeoutError):
        return SmtpTestErgebnis(
            False,
            f"Zeitüberschreitung (Timeout nach {timeout}s)",
            f"Der SMTP-Server auf {host}:{port} antwortet nicht innerhalb des Zeitlimits. Bitte Port und Firewall prüfen.",
        )
    except socket.gaierror as exc:
        return SmtpTestErgebnis(
            False,
            f"DNS-Fehler: Hostname „{host}“ nicht gefunden",
            f"Die Serveradresse konnte nicht aufgelöst werden: {exc}",
        )
    except ConnectionRefusedError:
        return SmtpTestErgebnis(
            False,
            f"Verbindung abgelehnt auf {host}:{port}",
            "Auf dem angegebenen Port läuft kein erreichbarer SMTP-Dienst.",
        )
    except ssl.SSLError as exc:
        return SmtpTestErgebnis(
            False,
            f"SSL/TLS-Verschlüsselungsfehler bei Verbindung zu {host}:{port}",
            f"Das Zertifikat oder Protokoll wurde abgelehnt: {exc}",
        )
    except smtplib.SMTPException as exc:
        return SmtpTestErgebnis(
            False,
            f"SMTP-Protokollfehler: {exc}",
            f"Der Mailserver meldete: {exc}",
        )
    except Exception as exc:
        logger.exception("Unerwarteter Fehler beim SMTP-Test zu %s:%s", host, port)
        return SmtpTestErgebnis(
            False,
            f"Verbindungsfehler: {exc}",
            str(exc),
        )
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass


def sende_test_email(
    empfaenger: str,
    host: str,
    port: int | str = 587,
    user: str = "",
    password: str = "",
    use_tls: bool = True,
    use_ssl: bool = False,
    from_email: str = "",
    timeout: int = 10,
) -> SmtpTestErgebnis:
    """Sendet eine echte Test-E-Mail über die angegebenen SMTP-Zugangsdaten."""
    empfaenger = (empfaenger or "").strip()
    if not empfaenger or "@" not in empfaenger:
        return SmtpTestErgebnis(False, "Ungültige Empfänger-E-Mail-Adresse für den Testversand.")

    from_email = (from_email or "").strip() or user or "termine@example.org"

    # Zunächst Auth testen
    auth_ergebnis = teste_smtp_authentifizierung(
        host=host,
        port=port,
        user=user,
        password=password,
        use_tls=use_tls,
        use_ssl=use_ssl,
        timeout=timeout,
    )
    if not auth_ergebnis.ok:
        return auth_ergebnis

    try:
        port_num = int(port or (465 if use_ssl else 587))
        connection = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=host,
            port=port_num,
            username=user or None,
            password=password or None,
            use_tls=use_tls,
            use_ssl=use_ssl,
            timeout=timeout,
            fail_silently=False,
        )

        betreff = "Test-E-Mail: SMTP-Konfiguration erfolgreich"
        text_inhalt = (
            "Hallo,\n\n"
            "diese Test-E-Mail bestätigt, dass die SMTP-Einstellungen in Ihrer "
            "Terminbuchungs-Anwendung erfolgreich eingerichtet wurden und der Mailversand funktioniert.\n\n"
            f"Server: {host}:{port_num}\n"
            f"Benutzer: {user or '(ohne)'}\n"
            f"Absender: {from_email}\n\n"
            "Viele Grüße,\nIhr Buchungssystem"
        )
        html_inhalt = (
            "<div style='font-family:sans-serif; max-width:560px; margin:0 auto; padding:20px; border:1px solid #e2e8f0; border-radius:8px;'>"
            "<h2 style='color:#065f46; margin-top:0;'>✓ SMTP-Konfiguration erfolgreich</h2>"
            "<p>Diese Test-E-Mail bestätigt, dass die E-Mail-Einstellungen in Ihrer Online-Terminbuchung einwandfrei funktionieren.</p>"
            "<table style='width:100%; border-collapse:collapse; margin:16px 0; font-size:14px;'>"
            f"<tr><td style='padding:6px 0; color:#64748b;'><strong>SMTP-Server:</strong></td><td style='padding:6px 0;'>{host}:{port_num}</td></tr>"
            f"<tr><td style='padding:6px 0; color:#64748b;'><strong>Benutzername:</strong></td><td style='padding:6px 0;'>{user or '–'}</td></tr>"
            f"<tr><td style='padding:6px 0; color:#64748b;'><strong>Absender:</strong></td><td style='padding:6px 0;'>{from_email}</td></tr>"
            "</table>"
            "<p style='font-size:12px; color:#94a3b8; margin-bottom:0;'>Automatisch generierte Testnachricht.</p>"
            "</div>"
        )

        msg = EmailMultiAlternatives(
            subject=betreff,
            body=text_inhalt,
            from_email=from_email,
            to=[empfaenger],
            connection=connection,
        )
        msg.attach_alternative(html_inhalt, "text/html")
        msg.send(fail_silently=False)

        return SmtpTestErgebnis(
            True,
            f"Test-E-Mail erfolgreich an „{empfaenger}“ versendet!",
            f"Absender: {from_email} über {host}:{port_num}",
        )

    except Exception as exc:
        logger.exception("Fehler beim Versenden der Test-E-Mail an %s", empfaenger)
        return SmtpTestErgebnis(
            False,
            f"Fehler beim Senden der Test-E-Mail: {exc}",
            str(exc),
        )
