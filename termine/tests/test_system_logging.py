from __future__ import annotations

import datetime as dt
import logging

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from termine.models.logging import LogKategorie, LogLevel, SystemLog
from termine.services.logging import cleanup_old_logs, log_event

User = get_user_model()


class SystemLoggingTest(TestCase):
    def setUp(self):
        self.inhaber = User.objects.create_superuser(
            username="inhaber", email="inhaber@example.com", password="pw"
        )
        self.mitarbeiter = User.objects.create_user(
            username="mitarbeiter", email="mitarbeiter@example.com", password="pw"
        )

    def test_log_event_erstellt_datenbanksatz(self):
        eintrag = log_event(
            kategorie="buchung",
            aktion="test_aktion",
            titel="Test-Buchung erstellt",
            nachricht="Zusatzinfo",
            level="INFO",
            details={"key": "value"},
        )
        self.assertIsNotNone(eintrag)
        self.assertEqual(eintrag.kategorie, LogKategorie.BUCHUNG)
        self.assertEqual(eintrag.level, LogLevel.INFO)
        self.assertEqual(eintrag.titel, "Test-Buchung erstellt")
        self.assertEqual(eintrag.details, {"key": "value"})

    def test_cleanup_old_logs(self):
        now = timezone.now()
        alt = SystemLog.objects.create(
            kategorie=LogKategorie.SYSTEM,
            aktion="alt",
            titel="Alter Log",
        )
        SystemLog.objects.filter(id=alt.id).update(created_at=now - dt.timedelta(days=40))

        neu = SystemLog.objects.create(
            kategorie=LogKategorie.SYSTEM,
            aktion="neu",
            titel="Frischer Log",
        )

        res = cleanup_old_logs(max_days=30, max_records=1000)
        self.assertEqual(res["geloescht_alter"], 1)
        self.assertFalse(SystemLog.objects.filter(id=alt.id).exists())
        self.assertTrue(SystemLog.objects.filter(id=neu.id).exists())

    def test_cleanup_old_logs_mengenbegrenzung(self):
        for i in range(12):
            SystemLog.objects.create(
                kategorie=LogKategorie.BUCHUNG,
                aktion=f"test_{i}",
                titel=f"Eintrag {i}",
            )
        self.assertEqual(SystemLog.objects.count(), 12)

        res = cleanup_old_logs(max_days=365, max_records=10)
        self.assertEqual(res["geloescht_ueberhang"], 2)
        self.assertEqual(SystemLog.objects.count(), 10)

    def test_system_logs_view_zugriff(self):
        url = reverse("termine:system_logs")

        # Nicht angemeldet -> Redirect zu Login
        res = self.client.get(url)
        self.assertEqual(res.status_code, 302)

        # Mitarbeiter ohne is_staff -> 403
        self.client.force_login(self.mitarbeiter)
        res = self.client.get(url)
        self.assertEqual(res.status_code, 403)

        # Inhaber (is_staff) -> 200 OK
        self.client.force_login(self.inhaber)
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "System- &amp; Audit-Logs")

    def test_system_logs_view_filter(self):
        self.client.force_login(self.inhaber)
        url = reverse("termine:system_logs")

        SystemLog.objects.create(
            level=LogLevel.ERROR,
            kategorie=LogKategorie.FSM,
            aktion="fsm_error",
            titel="FSM Gateway Fehler",
        )
        SystemLog.objects.create(
            level=LogLevel.INFO,
            kategorie=LogKategorie.BUCHUNG,
            aktion="buchung_ok",
            titel="Buchung erfolgreich",
        )

        # Filter Kategorie
        res = self.client.get(f"{url}?kategorie=fsm")
        self.assertContains(res, "FSM Gateway Fehler")
        self.assertNotContains(res, "Buchung erfolgreich")

        # Filter Level
        res = self.client.get(f"{url}?level=ERROR")
        self.assertContains(res, "FSM Gateway Fehler")
        self.assertNotContains(res, "Buchung erfolgreich")

        # Suche
        res = self.client.get(f"{url}?q=Buchung")
        self.assertContains(res, "Buchung erfolgreich")
        self.assertNotContains(res, "FSM Gateway Fehler")

    def test_cleanup_logs_action(self):
        self.client.force_login(self.inhaber)
        url = reverse("termine:cleanup_logs")

        res = self.client.post(url)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, reverse("termine:system_logs"))

    def test_db_log_handler_faengt_warning_ab(self):
        logger_test = logging.getLogger("termine.services.fsm_test")
        logger_test.warning("Warnung über standard logger: Testwert 98765")

        found = SystemLog.objects.filter(titel__icontains="Testwert 98765").exists()
        self.assertTrue(found)
