"""Tests für den Fahrschulmanager (FSM) API-Client."""

from __future__ import annotations

import datetime as dt
import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from termine.services.fsm_client import (
    FsmApiError,
    FsmAuthError,
    FsmClient,
    FsmError,
    FsmTermin,
)


def _mock_response(status_code: int = 200, data: dict | list | None = None) -> MagicMock:
    """Erstellt ein Mock-HTTP-Response-Objekt für urlopen."""
    body_str = json.dumps(data) if data is not None else ""
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = status_code
    mock_resp.read.return_value = body_str.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None
    return mock_resp


def _mock_http_error(status_code: int, data: dict | list | str | None = None) -> urllib.error.HTTPError:
    """Erstellt ein Mock-HTTPError-Objekt."""
    body_bytes = (
        json.dumps(data).encode("utf-8")
        if isinstance(data, (dict, list))
        else (data.encode("utf-8") if data else b"")
    )
    fp = io.BytesIO(body_bytes)
    return urllib.error.HTTPError(
        url="https://api.fahrschulmanager.de/v1/test",
        code=status_code,
        msg=f"HTTP {status_code}",
        hdrs={},  # type: ignore[arg-type]
        fp=fp,
    )


class FsmClientTests(SimpleTestCase):
    """Testet alle Methoden und Fehlerpfade des FSM-Clients."""

    def setUp(self):
        super().setUp()
        cache.clear()

    @override_settings(FSM_SYNC_ENABLED=True, FSM_API_KEY="test-api-key")
    def test_initialisierung_und_einstellungen(self):
        client = FsmClient()
        self.assertTrue(client.is_enabled)
        self.assertEqual(client.get_api_key(), "test-api-key")

        client.set_auth_token("mein-token")
        self.assertEqual(client.get_auth_token(), "mein-token")

    @override_settings(FSM_SYNC_ENABLED=False)
    def test_is_enabled_deaktiviert(self):
        client = FsmClient()
        self.assertFalse(client.is_enabled)

    def test_header_generierung(self):
        client = FsmClient(api_key="key123", auth_token="token456")
        headers = client._get_headers()
        self.assertEqual(headers["Authorization"], "Bearer token456")
        self.assertEqual(headers["x-fsm-apikey"], "key123")
        self.assertEqual(headers["Referer"], "https://portal.fahrschulmanager.de/")

    @patch("urllib.request.urlopen")
    def test_get_fahrlehrer_erfolg(self, mock_urlopen):
        mock_data = [
            {"id": "uuid-1", "vorname": "Jonas", "nachname": "Eisele", "name": "Eisele"},
            {"id": "uuid-2", "vorname": "Max", "nachname": "Hampel", "name": "Hampel"},
        ]
        mock_urlopen.return_value = _mock_response(200, mock_data)

        client = FsmClient(auth_token="valid-token")
        fahrlehrer = client.get_fahrlehrer()

        self.assertEqual(len(fahrlehrer), 2)
        self.assertEqual(fahrlehrer[0]["id"], "uuid-1")
        self.assertEqual(fahrlehrer[0]["nachname"], "Eisele")

    @patch("urllib.request.urlopen")
    def test_get_termine_erfolg(self, mock_urlopen):
        mock_data = [
            {
                "id": "termin-1",
                "von": "2026-08-15T10:00:00+02:00",
                "bis": "2026-08-15T11:20:00+02:00",
                "fidTerminart": "PS",
                "texte": "Fahrstunde",
                "schuelername": "Mustermann, Max",
            },
            {
                "id": "termin-2",
                "von": "2026-08-15T14:00:00+02:00",
                "bis": "2026-08-15T14:45:00+02:00",
                "fidTerminart": "PX",
                "texte": "Beratungen",
                "schuelername": None,
            },
        ]
        mock_urlopen.return_value = _mock_response(200, mock_data)

        client = FsmClient(auth_token="valid-token")
        von = dt.datetime(2026, 8, 15, 0, 0, tzinfo=dt.timezone.utc)
        bis = dt.datetime(2026, 8, 16, 0, 0, tzinfo=dt.timezone.utc)

        termine = client.get_termine("uuid-1", von, bis)

        self.assertEqual(len(termine), 2)
        self.assertIsInstance(termine[0], FsmTermin)
        self.assertEqual(termine[0].id, "termin-1")
        self.assertEqual(termine[0].terminart, "PS")
        self.assertEqual(termine[0].schueler_name, "Mustermann, Max")
        self.assertEqual(termine[1].titel, "Beratungen")

    @patch("urllib.request.urlopen")
    def test_termin_anlegen_erfolg(self, mock_urlopen):
        mock_response_data = {
            "viewModel": {
                "id": "neu-erstellte-uuid-123",
                "fidTerminart": "PX",
                "texte": "Beratung Max",
            },
            "responses": [],
        }
        mock_urlopen.return_value = _mock_response(201, mock_response_data)

        client = FsmClient(auth_token="valid-token")
        von = dt.datetime(2026, 8, 15, 14, 0, tzinfo=dt.timezone.utc)
        bis = dt.datetime(2026, 8, 15, 14, 45, tzinfo=dt.timezone.utc)

        termin_id = client.termin_anlegen(
            fahrlehrer_fsm_id="lehrer-uuid",
            von=von,
            bis=bis,
            titel="Beratung Max",
        )

        self.assertEqual(termin_id, "neu-erstellte-uuid-123")

    @patch("urllib.request.urlopen")
    def test_termin_anlegen_konflikt_400(self, mock_urlopen):
        err_payload = {
            "viewModel": None,
            "responses": [
                {
                    "question": 2,
                    "errorMessage": "Fahrlehrer bereits verplant von 14:00 bis 14:45 Uhr.",
                }
            ],
        }
        mock_urlopen.side_effect = _mock_http_error(400, err_payload)

        client = FsmClient(auth_token="valid-token")
        von = dt.datetime(2026, 8, 15, 14, 0, tzinfo=dt.timezone.utc)
        bis = dt.datetime(2026, 8, 15, 14, 45, tzinfo=dt.timezone.utc)

        with self.assertRaises(FsmApiError) as ctx:
            client.termin_anlegen("lehrer-uuid", von, bis, "Beratung")

        self.assertIn("Fahrlehrer bereits verplant", str(ctx.exception))
        self.assertEqual(ctx.exception.status_code, 400)

    @patch("urllib.request.urlopen")
    def test_auth_fehler_401(self, mock_urlopen):
        mock_urlopen.side_effect = _mock_http_error(401, {"error": "Unauthorized"})

        client = FsmClient(auth_token="abgelaufenes-token")

        with self.assertRaises(FsmAuthError):
            client.get_fahrlehrer()

    @patch("urllib.request.urlopen")
    def test_termin_loeschen_erfolg(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(200, {"viewModel": {"id": "termin-123"}})

        client = FsmClient(auth_token="valid-token")
        erfolg = client.termin_loeschen("termin-123")

        self.assertTrue(erfolg)

    @patch("urllib.request.urlopen")
    def test_netzwerkfehler_urlerror(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        client = FsmClient(auth_token="valid-token")

        with self.assertRaises(FsmError):
            client.get_fahrlehrer()
