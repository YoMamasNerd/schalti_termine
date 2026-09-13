"""Validatoren für Buchungsdaten (E-Mail und Telefonnummer)."""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator


def validate_email_address(value: str | None) -> str:
    """Validiert und normalisiert eine E-Mail-Adresse.

    Prüft Syntax, Vorhandensein einer vollständigen Domain mit gültiger TLD
    und bereinigt führende/nachfolgende Leerzeichen.
    Ausnahme: 'Gelöscht' (DSGVO-Anonymisierung) wird akzeptiert.
    """
    if not value or not str(value).strip():
        raise ValidationError("Bitte geben Sie eine E-Mail-Adresse an.")

    email = str(value).strip()
    if email == "Gelöscht":
        return email

    # Grundlegende Syntax-Prüfung über Djangos EmailValidator
    validator = EmailValidator(message="Bitte geben Sie eine gültige E-Mail-Adresse ein.")
    validator(email)

    if "@" not in email:
        raise ValidationError("Bitte geben Sie eine gültige E-Mail-Adresse ein.")

    user_part, domain_part = email.rsplit("@", 1)
    if not user_part or not domain_part:
        raise ValidationError("Bitte geben Sie eine gültige E-Mail-Adresse ein.")

    if "." not in domain_part:
        raise ValidationError(
            "Die Domain der E-Mail-Adresse ist unvollständig (z. B. .de oder .com fehlt)."
        )

    parts = domain_part.split(".")
    tld = parts[-1]
    if len(tld) < 2 or not tld.isalpha():
        raise ValidationError("Die Domain-Endung der E-Mail-Adresse ist ungültig.")

    if any(not p for p in parts):
        raise ValidationError("Die E-Mail-Adresse enthält ungültige Punkte.")

    return email.lower()


def validate_phone_number(value: str | None) -> str:
    """Validiert und normalisiert eine Telefonnummer.

    Die Telefonnummer ist freiwillig. Ist der Wert leer oder None,
    wird ein leerer String zurückgegeben und kein Fehler geworfen.
    Ausnahme: 'Gelöscht' (DSGVO-Anonymisierung) wird akzeptiert.
    """
    if not value or not str(value).strip():
        return ""

    raw = str(value).strip()
    if raw == "Gelöscht":
        return raw

    # Erlaubte Zeichen: Ziffern, Leerzeichen, +, -, /, (, )
    if not re.match(r"^[\d\s+\-/(/)]+$", raw):
        raise ValidationError(
            "Die Telefonnummer darf nur Ziffern, Leerzeichen und die Zeichen + - / ( ) enthalten."
        )

    # Pluszeichen nur am Anfang
    if "+" in raw and not raw.startswith("+"):
        raise ValidationError("Das Plus-Zeichen darf nur am Anfang einer Vorwahl stehen.")
    if raw.count("+") > 1:
        raise ValidationError("Die Telefonnummer enthält zu viele Plus-Zeichen.")

    digits = re.sub(r"\D", "", raw)
    if len(digits) < 6:
        raise ValidationError("Die Telefonnummer ist zu kurz (mindestens 6 Ziffern erforderlich).")
    if len(digits) > 15:
        raise ValidationError("Die Telefonnummer ist zu lang (maximal 15 Ziffern erlaubt).")

    # Keine offensichtlichen Dummy-Zahlen (z. B. 0000000, 1111111)
    if len(set(digits)) == 1:
        raise ValidationError("Bitte geben Sie eine gültige Telefonnummer an.")

    # Mehrfache Leerzeichen normalisieren
    normalized = re.sub(r"\s+", " ", raw)
    return normalized
