from django.core.management.base import BaseCommand

from termine.services.buchung import (
    abgelaufene_reservierungen_freigeben,
    alte_buchungen_anonymisieren,
    erinnerungen_versenden,
)


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

    def handle(self, *args, **optionen):
        freigegeben = abgelaufene_reservierungen_freigeben()
        self.stdout.write(f"{freigegeben} abgelaufene Reservierungen freigegeben.")

        if not optionen["ohne_erinnerungen"]:
            versendet = erinnerungen_versenden()
            self.stdout.write(f"{versendet} Erinnerungen versendet.")

        if not optionen["ohne_anonymisierung"]:
            anonymisiert = alte_buchungen_anonymisieren()
            self.stdout.write(f"{anonymisiert} alte Buchungen anonymisiert.")

        self.stdout.write(self.style.SUCCESS("Pflege abgeschlossen."))
