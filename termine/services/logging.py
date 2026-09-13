from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)


def log_event(
    kategorie: str,
    aktion: str,
    titel: str,
    nachricht: str = "",
    level: str = "INFO",
    benutzer=None,
    details: dict[str, Any] | None = None,
    request=None,
) -> Any:
    """
    Erstellt sicher einen SystemLog-Eintrag in der Datenbank.
    Fängt alle Fehler ab, damit der normale Ablauf der aufrufenden Funktion
    niemals unterbrochen wird.
    """
    try:
        from termine.models.logging import LogKategorie, LogLevel, SystemLog

        user = benutzer
        ip = None
        if request:
            if not user and getattr(request, "user", None) and request.user.is_authenticated:
                user = request.user
            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
            if x_forwarded_for:
                ip = x_forwarded_for.split(",")[0].strip()
            else:
                ip = request.META.get("REMOTE_ADDR")

        lvl = level.upper()
        if lvl not in LogLevel.values:
            lvl = LogLevel.INFO

        kat = kategorie.lower()
        if kat not in LogKategorie.values:
            kat = LogKategorie.SYSTEM

        import json
        json_details = {}
        if details:
            try:
                json_details = json.loads(json.dumps(details, default=str))
            except Exception:
                json_details = {"info": str(details)}

        from django.db import transaction
        with transaction.atomic():
            return SystemLog.objects.create(
                level=lvl,
                kategorie=kat,
                aktion=aktion[:100],
                titel=titel[:255],
                nachricht=str(nachricht or ""),
                benutzer=user if getattr(user, "is_authenticated", False) else None,
                details=json_details,
                ip_adresse=ip,
            )
    except Exception as exc:
        logger.error("Fehler beim Speichern des SystemLogs: %s", exc)
        return None


def cleanup_old_logs(max_days: int = 30, max_records: int = 10000) -> dict[str, int]:
    """
    Bereinigt die SystemLog-Tabelle:
    1. Löscht Einträge älter als max_days (Standard: 30 Tage).
    2. Begrenzt auf maximal max_records Einträge.
    """
    from termine.models.logging import SystemLog

    geloescht_alter = 0
    geloescht_ueberhang = 0

    stichtag = timezone.now() - timedelta(days=max_days)
    del_count, _ = SystemLog.objects.filter(created_at__lt=stichtag).delete()
    geloescht_alter = del_count

    total = SystemLog.objects.count()
    if total > max_records:
        ueberschuss = total - max_records
        alte_ids = list(
            SystemLog.objects.order_by("created_at")[:ueberschuss].values_list("id", flat=True)
        )
        if alte_ids:
            del_c, _ = SystemLog.objects.filter(id__in=alte_ids).delete()
            geloescht_ueberhang = del_c

    logger.info(
        "SystemLog-Bereinigung: %d nach Alter (> %d Tage), %d nach Mengenbegrenzung gelöscht.",
        geloescht_alter,
        max_days,
        geloescht_ueberhang,
    )
    return {
        "geloescht_alter": geloescht_alter,
        "geloescht_ueberhang": geloescht_ueberhang,
        "gesamt_geloescht": geloescht_alter + geloescht_ueberhang,
        "verbleibend": SystemLog.objects.count(),
    }


class DBLogHandler(logging.Handler):
    """
    Python-Logging-Handler, der Logs ab WARNING direkt in die SystemLog-Tabelle schreibt.
    """

    _in_emit = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._in_emit:
            return

        if record.levelno < logging.WARNING:
            return

        if record.name.startswith("termine.services.logging") or record.name.startswith("django.db"):
            return

        try:
            self._in_emit = True
            msg = self.format(record)
            kat = "system"
            if "fsm" in record.name:
                kat = "fsm"
            elif "buchung" in record.name:
                kat = "buchung"
            elif "termin" in record.name or "planung" in record.name:
                kat = "termin"

            lvl = "WARNING" if record.levelno < logging.ERROR else "ERROR"
            if record.levelno >= logging.CRITICAL:
                lvl = "CRITICAL"

            log_event(
                kategorie=kat,
                aktion=f"log_{record.levelname.lower()}",
                titel=f"{record.name}: {record.getMessage()[:200]}",
                nachricht=msg,
                level=lvl,
                details={
                    "module": record.module,
                    "filename": record.filename,
                    "lineno": record.lineno,
                    "funcName": record.funcName,
                },
            )
        except Exception:
            pass
        finally:
            self._in_emit = False
