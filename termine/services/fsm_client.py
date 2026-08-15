"""Fahrschulmanager (FSM) REST-API Client.

Ermöglicht die optionale Synchronisation von Beratungsterminen und
Belegungszeiten mit dem externen Fahrschulmanager-Portal.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import http.cookiejar
import json
import logging
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid
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

    def _pkce_pair(self) -> tuple[str, str]:
        """Erzeugt ein PKCE Verifier/Challenge-Paar für den OAuth2-Login."""
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        return verifier, challenge

    def auto_login(self, email: str | None = None, password: str | None = None) -> str:
        """Führt einen vollautomatischen OAuth2-Login gegen FSM durch."""
        email = (email or getattr(settings, "FSM_EMAIL", "")).strip().strip("'\"")
        password = (password or getattr(settings, "FSM_PASSWORD", "")).strip().strip("'\"")

        if not email or not password:
            raise FsmConfigError("FSM_EMAIL und FSM_PASSWORD sind nicht konfiguriert.")

        logger.info("FSM: Führe automatischen Login für %s durch...", email)

        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

        state = base64.urlsafe_b64encode(secrets.token_bytes(24)).rstrip(b"=").decode("ascii")
        verifier, challenge = self._pkce_pair()

        # 1. Authorize GET
        auth_params = {
            "response_type": "code",
            "client_id": "fsm",
            "redirect_uri": "https://portal.fahrschulmanager.de/login",
            "scope": "openid profile offline_access fsm_api",
            "state": state,
            "nonce": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_url = "https://login.fahren-lernen.de/connect/authorize?" + urllib.parse.urlencode(auth_params)

        try:
            resp = opener.open(auth_url, timeout=self.timeout)
            login_url = resp.geturl()
            html = resp.read().decode("utf-8")
        except Exception as exc:
            raise FsmAuthError(f"OAuth Authorize fehlgeschlagen: {exc}") from exc

        xsrf_match = re.search(r"<meta\s+name=[\"']xsrf[\"']\s+content=[\"']([^\"']+)[\"']", html)
        if not xsrf_match:
            xsrf_match = re.search(r"content=[\"']([^\"']+)[\"']\s+name=[\"']xsrf[\"']", html)
        if not xsrf_match:
            raise FsmAuthError("XSRF-Token konnte auf der FSM-Loginseite nicht gefunden werden.")
        xsrf = xsrf_match.group(1)

        parsed = urllib.parse.urlparse(login_url)
        qs = urllib.parse.parse_qs(parsed.query)
        return_url = qs.get("ReturnUrl", ["/connect/authorize/callback"])[0]

        # 2. Form POST
        boundary = "----WebKitFormBoundary" + secrets.token_hex(16)
        form_fields = [
            ("password", password),
            ("username", email),
            ("__RequestVerificationToken", xsrf),
            ("returnUrl", return_url),
            ("rememberLogin", "false"),
            ("button", "login"),
        ]

        body_parts = []
        for k, v in form_fields:
            body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
        body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        multipart_body = b"".join(body_parts)

        post_url = f"https://login.fahren-lernen.de/account/login?ReturnUrl={urllib.parse.quote(return_url)}"
        post_req = urllib.request.Request(
            post_url,
            data=multipart_body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "text/html, application/xhtml+xml, application/xml;q=0.9, */*;q=0.8",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": login_url,
            },
            method="POST",
        )

        try:
            post_resp = opener.open(post_req, timeout=self.timeout)
            callback_path = json.loads(post_resp.read().decode("utf-8"))
            callback_url = urllib.parse.urljoin("https://login.fahren-lernen.de", callback_path)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="ignore").strip().strip('"')
            logger.warning("FSM Login fehlgeschlagen: %s (%s)", err_body, exc.code)
            raise FsmAuthError(f"FSM Login fehlgeschlagen: {err_body}") from exc
        except Exception as exc:
            raise FsmAuthError(f"FSM Login fehlgeschlagen: {exc}") from exc

        # 3. Callback
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        cb_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), NoRedirect)
        code = None
        try:
            cb_opener.open(callback_url, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if e.code == 302:
                loc = e.headers["Location"]
                code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get("code", [None])[0]

        if not code:
            raise FsmAuthError("FSM OAuth-Callback lieferte keinen Authorization Code.")

        # 4. Exchange code for access token
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://portal.fahrschulmanager.de/login",
            "code_verifier": verifier,
            "client_id": "fsm",
        }
        token_req = urllib.request.Request(
            "https://login.fahren-lernen.de/connect/token",
            data=urllib.parse.urlencode(token_data).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0",
            },
        )
        try:
            with urllib.request.urlopen(token_req, timeout=self.timeout) as tr:
                res = json.loads(tr.read().decode("utf-8"))
                oauth_access_token = res["access_token"]
        except Exception as exc:
            raise FsmAuthError(f"Token-Austausch bei connect/token fehlgeschlagen: {exc}") from exc

        # 5. Exchange via POST /v1/auth/sso
        hardware_id = str(uuid.uuid4())
        sso_payload = json.dumps({"viewModel": {"access_token": oauth_access_token}}).encode("utf-8")
        sso_req = urllib.request.Request(
            f"{self.base_url}/auth/sso?hardwareId={hardware_id}",
            data=sso_payload,
            headers={
                "x-fsm-apikey": self.get_api_key(),
                "Referer": "https://portal.fahrschulmanager.de/",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(sso_req, timeout=self.timeout) as sso_resp:
                sso_data = json.loads(sso_resp.read().decode("utf-8"))
                final_auth_token = sso_data["viewModel"]["authToken"]
        except Exception as exc:
            raise FsmAuthError(f"SSO-Authentifizierung an FSM-API fehlgeschlagen: {exc}") from exc

        # Cache for 12 hours (43200 seconds)
        self.set_auth_token(final_auth_token, timeout=43200)
        logger.info("FSM: Automatischer Login erfolgreich. Neues Auth-Token hinterlegt.")
        return final_auth_token

    def get_auth_token(self) -> str | None:
        """Liefert das aktuelle Auth-Token aus Cache, Konfiguration oder Auto-Login."""
        if self._auth_token:
            return self._auth_token
        cached = cache.get(CACHE_KEY_TOKEN)
        if cached:
            return cached
        token_env = getattr(settings, "FSM_AUTH_TOKEN", "")
        if token_env:
            return token_env

        # Versuche Auto-Login falls Zugangsdaten konfiguriert sind
        email = getattr(settings, "FSM_EMAIL", "")
        password = getattr(settings, "FSM_PASSWORD", "")
        if email and password:
            try:
                return self.auto_login(email, password)
            except Exception as exc:
                logger.warning("FSM Auto-Login fehlgeschlagen: %s", exc)
                return None
        return None

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
        retry_on_401: bool = True,
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
                logger.warning("FSM API: Nicht autorisiert (401). Prüfe Auto-Login...")
                if retry_on_401 and getattr(settings, "FSM_EMAIL", "") and getattr(settings, "FSM_PASSWORD", ""):
                    try:
                        self.auto_login()
                        return self._request(method, path, params=params, data=data, retry_on_401=False)
                    except Exception as login_exc:
                        logger.warning("FSM API: Re-Authentifizierung per Auto-Login fehlgeschlagen: %s", login_exc)
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
            for row in res:
                vorname = (row.get("vorname") or "").strip()
                nachname = (row.get("nachname") or "").strip()
                voller_name = f"{vorname} {nachname}".strip()
                if not voller_name:
                    voller_name = row.get("displayName") or row.get("name") or "Unbekannt"
                row["voller_name"] = voller_name
                row["name"] = voller_name
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

    def termin_aktualisieren(
        self,
        fsm_termin_id: str,
        fahrlehrer_fsm_id: str,
        von: dt.datetime,
        bis: dt.datetime,
        titel: str,
        leistungsart_id: str | None = None,
        terminart: str = "PX",
    ) -> bool:
        """Aktualisiert einen bestehenden Termin (z. B. Beschreibung oder Status) in FSM per PUT."""
        fid_leistung = leistungsart_id or getattr(
            settings,
            "FSM_LEISTUNGSART_ID",
            "4330ec51-91b9-45f1-a3fb-88179db000ce",
        )

        von_iso = von.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        bis_iso = bis.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        payload = {
            "viewModel": {
                "id": fsm_termin_id,
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

        res = self._request("PUT", "termine", data=payload)
        return res is not None

    def termin_loeschen(self, fsm_termin_id: str) -> bool:
        """Löscht einen Termin anhand seiner FSM-Termin-UUID."""
        payload = {
            "viewModel": {
                "id": fsm_termin_id,
            }
        }
        res = self._request("DELETE", "termine", data=payload)
        return res is not None
