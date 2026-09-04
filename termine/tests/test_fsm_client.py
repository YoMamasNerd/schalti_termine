"""Tests für den FSM-Gateway Client."""

from __future__ import annotations

import datetime as dt
import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from termine.services.fsm_client import (
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
        url="http://127.0.0.1:8090/v1/test",
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

    @override_settings(FSM_SYNC_ENABLED=True, FSM_GATEWAY_URL="http://127.0.0.1:8090/v1")
    def test_initialisierung_und_einstellungen(self):
        client = FsmClient()
        self.assertTrue(client.is_enabled)
        self.assertEqual(client.gateway_url, "http://127.0.0.1:8090/v1")

    @override_settings(FSM_SYNC_ENABLED=False)
    def test_is_enabled_deaktiviert(self):
        client = FsmClient()
        self.assertFalse(client.is_enabled)

    @patch("urllib.request.urlopen")
    def test_get_fahrlehrer_erfolg(self, mock_urlopen):
        mock_data = {
            "count": 2,
            "fahrlehrer": [
                {"id": "uuid-1", "vorname": "Jonas", "nachname": "Eisele", "name": "Jonas Eisele"},
                {"id": "uuid-2", "vorname": "Max", "nachname": "Hampel", "name": "Max Hampel"},
            ],
        }
        mock_urlopen.return_value = _mock_response(200, mock_data)

        client = FsmClient()
        fahrlehrer = client.get_fahrlehrer()

        self.assertEqual(len(fahrlehrer), 2)
        self.assertEqual(fahrlehrer[0]["id"], "uuid-1")
        self.assertEqual(fahrlehrer[0]["nachname"], "Eisele")

    @patch("urllib.request.urlopen")
    def test_get_termine_erfolg(self, mock_urlopen):
        mock_data = {
            "fahrlehrer_id": "uuid-1",
            "start": "2026-08-15T00:00:00",
            "end": "2026-08-15T23:59:59",
            "count": 1,
            "events": [
                {
                    "id": "termin-1",
                    "von": "2026-08-15T10:00:00+02:00",
                    "bis": "2026-08-15T11:20:00+02:00",
                    "fahrlehrer_id": "uuid-1",
                    "terminart": "FS",
                    "titel": "Fahrstunde",
                    "schueler_name": "Max Mustermann",
                    "ist_fahrstunde": True,
                    "dauer_minuten": 80.0,
                }
            ],
        }
        mock_urlopen.return_value = _mock_response(200, mock_data)

        client = FsmClient()
        start = dt.datetime(2026, 8, 15, 0, 0)
        end = dt.datetime(2026, 8, 15, 23, 59)
        termine = client.get_termine("uuid-1", start, end)

        self.assertEqual(len(termine), 1)
        termin = termine[0]
        self.assertIsInstance(termin, FsmTermin)
        self.assertEqual(termin.id, "termin-1")
        self.assertEqual(termin.terminart, "FS")
        self.assertEqual(termin.schueler_name, "Max Mustermann")

    @patch("urllib.request.urlopen")
    def test_termin_anlegen_erfolg(self, mock_urlopen):
        mock_data = {
            "success": True,
            "created_ids": ["neue-termin-uuid"],
            "count": 1,
        }
        mock_urlopen.return_value = _mock_response(200, mock_data)

        client = FsmClient()
        von = dt.datetime(2026, 8, 15, 14, 0)
        bis = dt.datetime(2026, 8, 15, 14, 45)
        termin_id = client.termin_anlegen("uuid-1", von, bis, "Beratungstermin")

        self.assertEqual(termin_id, "neue-termin-uuid")

    @patch("urllib.request.urlopen")
    def test_termin_loeschen_erfolg(self, mock_urlopen):
        mock_data = {"success": True, "deleted_id": "termin-123"}
        mock_urlopen.return_value = _mock_response(200, mock_data)

        client = FsmClient()
        success = client.termin_loeschen("termin-123")
        self.assertTrue(success)

    @patch("urllib.request.urlopen")
    def test_fehlerbehandlung_401(self, mock_urlopen):
        mock_urlopen.side_effect = _mock_http_error(401, {"detail": "Nicht autorisiert"})

        client = FsmClient()
        with self.assertRaises(FsmAuthError):
            client.get_fahrlehrer()

    @patch("urllib.request.urlopen")
    def test_fehlerbehandlung_netzwerk(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        client = FsmClient()
        with self.assertRaises(FsmError):
            client.get_fahrlehrer()
