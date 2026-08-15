"""Fahrschulmanager (FSM) REST-API Client.

Ermöglicht die optionale Synchronisation von Beratungsterminen und
Belegungszeiten mit dem externen Fahrschulmanager-Portal.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_KEY_TOKEN = "fsm_auth_token"
CACHE_KEY_API_KEY = "fsm_api_key"


class FsmError(Exception):
    """Basisklasse für alle FSM-Fehler."""


class FsmConfigError(FsmError):
    """Wird geworfen, wenn FSM-Einstellungen fehlen oder ungültig sind."""


class FsmAuthError(FsmError):
    """Wird geworfen, wenn die Authentifizierung fehlschlägt."""


class FsmApiError(FsmError):
    """Wird geworfen, wenn die FSM-API einen Fehlercode liefert."""

    def __init__(self, status_code: int, message: str, response_body: Any = None):
        super().__init__(f"FSM API-Fehler {status_code}: {message}")
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


class FsmClient:
    """Client für die Kommunikation mit der FSM REST-API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        auth_token: str | None = None,
        timeout: int = 10,
    ):
        self.base_url = (base_url or getattr(settings, "FSM_BASE_URL", "https://api.fahrschulmanager.de/v1")).rstrip("/")
        self._api_key = api_key or getattr(settings, "FSM_API_KEY", "")
        self._auth_token = auth_token
        self.timeout = timeout

    @property
    def is_enabled(self) -> bool:
        """Prüft, ob die FSM-Synchronisation global aktiv ist."""
        return getattr(settings, "FSM_SYNC_ENABLED", False)

    def get_auth_token(self) -> str | None:
        """Liefert das aktuelle Auth-Token aus Cache oder Konfiguration."""
        if self._auth_token:
            return self._auth_token
        return cache.get(CACHE_KEY_TOKEN)

    def set_auth_token(self, token: str, timeout: int = 86400) -> None:
        """Speichert ein neues Auth-Token im Cache."""
        self._auth_token = token
        cache.set(CACHE_KEY_TOKEN, token, timeout=timeout)

    def get_api_key(self) -> str:
        """Liefert den aktuellen FSM API-Schlüssel."""
        return self._api_key or cache.get(CACHE_KEY_API_KEY) or ""

    def set_api_key(self, key: str) -> None:
        """Speichert den API-Key."""
        self._api_key = key
        cache.set(CACHE_KEY_API_KEY, key, timeout=None)

    def _get_headers(self) -> dict[str, str]:
        """Erstellt die benötigten Standard-Header für API-Anfragen."""
        token = self.get_auth_token()
        api_key = self.get_api_key()

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": "SchaltiTermine-Sync/1.0",
            "Referer": "https://portal.fahrschulmanager.de/",
        }
        if token:
            headers["Authorization"] = token if token.startswith("Bearer ") else f"Bearer {token}"
        if api_key:
            headers["x-fsm-apikey"] = api_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Führt eine HTTP-Anfrage gegen die FSM-API aus."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"

        headers = self._get_headers()
        body_bytes = json.dumps(data).encode("utf-8") if data is not None else None

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                status = response.getcode()
                raw_body = response.read().decode("utf-8")
                if not raw_body.strip():
                    return None
                try:
                    return json.loads(raw_body)
                except json.JSONDecodeError:
                    return raw_body
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 401:
                logger.warning("FSM API: Nicht autorisiert (401). Token möglicherweise abgelaufen.")
                raise FsmAuthError("FSM Authentifizierung fehlgeschlagen (401).") from exc
            if exc.code == 400:
                logger.warning("FSM API: Bad Request (400): %s", err_body[:200])
                try:
                    err_json = json.loads(err_body)
                    responses = err_json.get("responses", [])
                    if responses and "errorMessage" in responses[0]:
                        msg = responses[0]["errorMessage"]
                        raise FsmApiError(400, msg, response_body=err_json) from exc
                except (json.JSONDecodeError, KeyError):
                    pass
            raise FsmApiError(exc.code, err_body, response_body=err_body) from exc
        except urllib.error.URLError as exc:
            logger.error("FSM API: Netzwerkfehler bei Verbindung zu %s: %s", url, exc)
            raise FsmError(f"Verbindung zu FSM fehlgeschlagen: {exc}") from exc

    # --- Fachliche Methoden ------------------------------------------------

    def get_fahrlehrer(self) -> list[dict[str, Any]]:
        """Ruft alle aktiven Fahrlehrer aus FSM ab."""
        res = self._request("GET", "lehrer/fahrlehrer", params={"onlyActive": "true"})
        if isinstance(res, list):
            return res
        return []

    def get_termine(
        self,
        fahrlehrer_fsm_id: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> list[FsmTermin]:
        """Ruft alle Termine und Fahrstunden eines Fahrlehrers für einen Zeitraum ab."""
        start_iso = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end_iso = end.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        params = {
            "onlyBuchbar": "false",
            "start": start_iso,
            "end": end_iso,
            "displayBegleitfahrzeug": "false",
            "skipDeleted": "true",
        }

        path = f"termine/lehrer/{fahrlehrer_fsm_id}"
        data = self._request("GET", path, params=params)

        if not isinstance(data, list):
            return []

        termine: list[FsmTermin] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            von_raw = row.get("von")
            bis_raw = row.get("bis")
            if not von_raw or not bis_raw:
                continue

            try:
                von_dt = dt.datetime.fromisoformat(von_raw)
                bis_dt = dt.datetime.fromisoformat(bis_raw)
            except ValueError:
                continue

            termine.append(
                FsmTermin(
                    id=str(row.get("id", "")),
                    von=von_dt,
                    bis=bis_dt,
                    fahrlehrer_id=fahrlehrer_fsm_id,
                    terminart=str(row.get("fidTerminart", "")),
                    titel=str(row.get("texte") or ""),
                    schueler_name=row.get("schuelername"),
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
    ) -> str:
        """Erstellt einen neuen Beratungstermin im FSM-Kalender des Fahrlehrers.

        Gibt die erzeugte FSM-Termin-UUID zurück.
        """
        fid_leistung = leistungsart_id or getattr(
            settings,
            "FSM_LEISTUNGSART_ID",
            "4330ec51-91b9-45f1-a3fb-88179db000ce",
        )

        von_iso = von.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        bis_iso = bis.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        payload = {
            "viewModel": {
                "von": von_iso,
                "bis": bis_iso,
                "fidFahrlehrer": [fahrlehrer_fsm_id],
                "fidTerminart": terminart,
                "gebucht": False,
                "fidFahrzeug": None,
                "fidLeistungsart": fid_leistung,
                "texte": titel,
            }
        }

        res = self._request("POST", "termine", data=payload)
        if isinstance(res, dict):
            created_id = res.get("viewModel", {}).get("id")
            if created_id:
                return str(created_id)

        raise FsmApiError(500, "Keine Termin-ID in der FSM-Antwort enthalten.", response_body=res)

    def termin_loeschen(self, fsm_termin_id: str) -> bool:
        """Löscht einen Termin anhand seiner FSM-Termin-UUID."""
        payload = {
            "viewModel": {
                "id": fsm_termin_id,
            }
        }
        res = self._request("DELETE", "termine", data=payload)
        return res is not None
