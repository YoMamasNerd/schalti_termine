"""FSM-Gateway Client for schalti_termine.

Kommuniziert mit dem zentralen FSM-Gateway Microservice für Fahrlehrer-, Kalender-
und Termin-Synchronisation.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


class FsmError(Exception):
    """Basisklasse für alle FSM-Gateway Fehler."""


class FsmConfigError(FsmError):
    """Wird geworfen, wenn Gateway-Einstellungen fehlen oder ungültig sind."""


class FsmAuthError(FsmError):
    """Wird geworfen, wenn die Authentifizierung fehlschlägt."""


class FsmApiError(FsmError):
    """Wird geworfen, wenn das Gateway einen HTTP-Fehlerstatus liefert."""

    def __init__(self, status_code: int, message: str, response_body: Any = None):
        super().__init__(f"FSM Gateway Fehler {status_code}: {message}")
        self.status_code = status_code
        self.response_body = response_body


@dataclass
class FsmTermin:
    """Repräsentiert einen Termin aus dem FSM-Kalender."""

    id: str
    von: dt.datetime
    bis: dt.datetime
    fahrlehrer_id: str
    terminart: str
    titel: str
    schueler_name: str | None = None
    ist_fahrstunde: bool = False
    ist_theorie: bool = False
    ist_blocker: bool = False


class FsmClient:
    """Client für die Kommunikation mit dem zentralen FSM-Gateway."""

    def __init__(
        self,
        gateway_url: str | None = None,
        api_key: str | None = None,
        timeout: int = 10,
        **kwargs: Any,
    ):
        base = gateway_url or getattr(settings, "FSM_GATEWAY_URL", getattr(settings, "FSM_BASE_URL", "http://127.0.0.1:8090/v1"))
        self.gateway_url = base.rstrip("/")
        self.api_key = api_key or getattr(settings, "FSM_GATEWAY_API_KEY", "")
        self.timeout = timeout

    @property
    def is_enabled(self) -> bool:
        """Prüft, ob die FSM-Synchronisation global aktiv ist."""
        return getattr(settings, "FSM_SYNC_ENABLED", False)

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | list[Any] | None = None,
    ) -> Any:
        """Führt eine HTTP-Anfrage an das FSM-Gateway aus."""
        clean_path = path.lstrip("/")
        url = f"{self.gateway_url}/{clean_path}"

        if params:
            query_str = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if query_str:
                url = f"{url}?{query_str}"

        req_body = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "schalti_termine/1.0",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        if data is not None:
            req_body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=req_body, headers=headers, method=method.upper())

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw.strip():
                    return None
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            err_msg = f"HTTP {exc.code}"
            try:
                err_json = json.loads(err_body)
                if isinstance(err_json, dict):
                    err_msg = err_json.get("detail") or err_json.get("message") or err_msg
            except Exception:
                pass

            if exc.code == 401:
                raise FsmAuthError(f"Gateway Authentifizierung fehlgeschlagen (401): {err_msg}") from exc
            raise FsmApiError(exc.code, str(err_msg), response_body=err_body) from exc
        except urllib.error.URLError as exc:
            logger.error("FSM-Gateway: Verbindungsfehler zu %s: %s", url, exc)
            raise FsmError(f"Verbindung zum FSM-Gateway fehlgeschlagen ({url}): {exc}") from exc

    def get_fahrlehrer(self) -> list[dict[str, Any]]:
        """Ruft alle aktiven Fahrlehrer über das Gateway ab."""
        res = self._request("GET", "fahrlehrer")
        if isinstance(res, dict) and "fahrlehrer" in res:
            return res["fahrlehrer"]
        if isinstance(res, list):
            return res
        return []

    def get_termine(
        self,
        fahrlehrer_fsm_id: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> list[FsmTermin]:
        """Ruft alle Termine eines Fahrlehrers für einen Zeitraum ab."""
        params = {
            "von": start.isoformat(),
            "bis": end.isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        res = self._request("GET", f"kalender/{fahrlehrer_fsm_id}", params=params)

        raw_events = []
        if isinstance(res, dict) and "events" in res:
            raw_events = res["events"]
        elif isinstance(res, list):
            raw_events = res

        termine: list[FsmTermin] = []
        for ev in raw_events:
            if not isinstance(ev, dict):
                continue
            von_raw = ev.get("von")
            bis_raw = ev.get("bis")
            if not von_raw or not bis_raw:
                continue

            try:
                von_dt = dt.datetime.fromisoformat(str(von_raw).replace("Z", "+00:00"))
                bis_dt = dt.datetime.fromisoformat(str(bis_raw).replace("Z", "+00:00"))
            except ValueError:
                continue

            termine.append(
                FsmTermin(
                    id=str(ev.get("id", "")),
                    von=von_dt,
                    bis=bis_dt,
                    fahrlehrer_id=fahrlehrer_fsm_id,
                    terminart=str(ev.get("terminart") or ev.get("fidTerminart") or ""),
                    titel=str(ev.get("titel") or ev.get("texte") or ""),
                    schueler_name=ev.get("schueler_name") or ev.get("schuelername"),
                    ist_fahrstunde=bool(ev.get("ist_fahrstunde", False)),
                    ist_theorie=bool(ev.get("ist_theorie", False)),
                    ist_blocker=bool(ev.get("ist_blocker", False)),
                )
            )

        return termine

    def termin_anlegen(
        self,
        fahrlehrer_fsm_id: str,
        von: dt.datetime,
        bis: dt.datetime,
        titel: str,
        leistungsart_id: str | None = None,
        terminart: str = "PX",
        schueler_id: str | None = None,
        fahrzeug_id: str | None = None,
        gebucht: bool = False,
    ) -> str:
        """Erstellt einen Termin/Blocker über das Gateway (inkl. Auto-Chunking)."""
        fid_leistung = leistungsart_id or getattr(
            settings,
            "FSM_LEISTUNGSART_ID",
            "4330ec51-91b9-45f1-a3fb-88179db000ce",
        )

        payload = {
            "fahrlehrer_id": fahrlehrer_fsm_id,
            "von": von.isoformat(),
            "bis": bis.isoformat(),
            "titel": titel,
            "terminart": terminart,
            "leistungsart_id": fid_leistung,
            "schueler_id": schueler_id,
            "fahrzeug_id": fahrzeug_id,
            "gebucht": gebucht,
        }

        res = self._request("POST", "termine", data=payload)
        if isinstance(res, dict):
            created_ids = res.get("created_ids", [])
            if created_ids:
                return str(created_ids[0])
            if "id" in res:
                return str(res["id"])
            if "termin_id" in res:
                return str(res["termin_id"])

        raise FsmApiError(500, "Keine Termin-ID in der Gateway-Antwort enthalten.", response_body=res)

    def termin_aktualisieren(
        self,
        fsm_termin_id: str,
        fahrlehrer_fsm_id: str,
        von: dt.datetime,
        bis: dt.datetime,
        titel: str,
        leistungsart_id: str | None = None,
        terminart: str = "PX",
        schueler_id: str | None = None,
        fahrzeug_id: str | None = None,
        gebucht: bool = False,
    ) -> bool:
        """Aktualisiert einen bestehenden Termin über das Gateway."""
        fid_leistung = leistungsart_id or getattr(
            settings,
            "FSM_LEISTUNGSART_ID",
            "4330ec51-91b9-45f1-a3fb-88179db000ce",
        )

        payload = {
            "fahrlehrer_id": fahrlehrer_fsm_id,
            "von": von.isoformat(),
            "bis": bis.isoformat(),
            "titel": titel,
            "terminart": terminart,
            "leistungsart_id": fid_leistung,
            "schueler_id": schueler_id,
            "fahrzeug_id": fahrzeug_id,
            "gebucht": gebucht,
        }

        res = self._request("PUT", f"termine/{fsm_termin_id}", data=payload)
        return bool(isinstance(res, dict) and res.get("success", True))

    def termin_loeschen(self, fsm_termin_id: str) -> bool:
        """Löscht einen Termin anhand seiner UUID über das Gateway."""
        res = self._request("DELETE", f"termine/{fsm_termin_id}")
        return bool(isinstance(res, dict) and res.get("success", True))
