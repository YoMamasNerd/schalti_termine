from __future__ import annotations

import secrets

WOCHENTAGE: tuple[tuple[int, str], ...] = (
    (0, "Montag"),
    (1, "Dienstag"),
    (2, "Mittwoch"),
    (3, "Donnerstag"),
    (4, "Freitag"),
    (5, "Samstag"),
    (6, "Sonntag"),
)

WOCHENTAG_KURZ = {0: "Mo", 1: "Di", 2: "Mi", 3: "Do", 4: "Fr", 5: "Sa", 6: "So"}

FUEHRERSCHEINKLASSEN: tuple[tuple[str, str], ...] = (
    ("AM", "AM – Roller / Kleinkraftrad"),
    ("A1", "A1 – Leichtkraftrad"),
    ("A2", "A2 – Motorrad (mittel)"),
    ("A", "A – Motorrad (unbeschränkt)"),
    ("B", "B – PKW"),
    ("B197", "B197 – PKW (Automatik-Regelung)"),
    ("B96", "B96 – PKW mit Anhänger"),
    ("BE", "BE – PKW mit Anhänger"),
    ("C1", "C1 – LKW bis 7,5 t"),
    ("C", "C – LKW"),
    ("CE", "CE – LKW mit Anhänger"),
    ("D1", "D1 – Kleinbus"),
    ("D", "D – Bus"),
    ("L", "L – Land-/Forstwirtschaft"),
    ("T", "T – Zugmaschine"),
    ("MOFA", "Mofa-Prüfbescheinigung"),
    ("SONST", "Andere / weiß ich noch nicht"),
)


def neuer_token() -> str:
    """Kryptografisch sicherer Token für Links in E-Mails und Kalender-Feeds."""
    return secrets.token_urlsafe(32)
