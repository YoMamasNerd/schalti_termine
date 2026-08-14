import logging

from django.core.management.base import BaseCommand

from termine.services.buchung import (
    abgelaufene_reservierungen_freigeben,
    alte_buchungen_anonymisieren,
    erinnerungen_versenden,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Laufende Pflege der Buchungen: abgelaufene Reservierungen freigeben, "
        "Erinnerungen verschicken und alte Daten anonymisieren."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--ohne-erinnerungen", action="store_true", help="Keine Erinnerungsmails verschicken."
        )
        parser.add_argument(
            "--ohne-anonymisierung",
            action="store_true",
            help="Alte Buchungen nicht anonymisieren.",
        )

    def _schritt(self, name: str, arbeit, meldung: str) -> bool:
        """Führt einen Pflegeschritt aus und lässt die anderen davon unberührt.

        Die drei Schritte haben nichts miteinander zu tun: Ein klemmender
        Mailserver darf nicht dazu führen, dass auch die DSGVO-Anonymisierung
        ausfällt – und zwar jede fünf Minuten aufs Neue, ohne dass es jemandem
        auffällt.
        """
        try:
            anzahl = arbeit()
        except Exception:  # noqa: BLE001 – ein Schritt darf die übrigen nicht mitreißen
            logger.exception("Pflegeschritt „%s“ fehlgeschlagen", name)
            self.stderr.write(f"{name}: fehlgeschlagen (Einzelheiten im Log).")
            return False
        self.stdout.write(meldung.format(anzahl=anzahl))
        return True

    def handle(self, *args, **optionen):
        vollstaendig = self._schritt(
            "Reservierungen freigeben",
            abgelaufene_reservierungen_freigeben,
            "{anzahl} abgelaufene Reservierungen freigegeben.",
        )

        if not optionen["ohne_erinnerungen"]:
            vollstaendig &= self._schritt(
                "Erinnerungen versenden",
                erinnerungen_versenden,
                "{anzahl} Erinnerungen versendet.",
            )

        if not optionen["ohne_anonymisierung"]:
            vollstaendig &= self._schritt(
                "Alte Buchungen anonymisieren",
                alte_buchungen_anonymisieren,
                "{anzahl} alte Buchungen anonymisiert.",
            )

        if vollstaendig:
            self.stdout.write(self.style.SUCCESS("Pflege abgeschlossen."))
        else:
            # Rückgabewert ungleich 0, damit cron und der Worker es merken.
            self.stderr.write("Pflege mit Fehlern abgeschlossen.")
            raise SystemExit(1)
