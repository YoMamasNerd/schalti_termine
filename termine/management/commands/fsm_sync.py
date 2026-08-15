from django.core.management.base import BaseCommand

from termine.models import Fahrlehrer
from termine.services.fsm_sync import sync_alle_fahrlehrer, sync_blocker_fuer_fahrlehrer


class Command(BaseCommand):
    help = "Synchronisiert Belegungszeiten und Fahrstunden aus dem Fahrschulmanager (FSM)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fahrlehrer",
            type=str,
            help="Slug oder ID eines spezifischen Fahrlehrers (optional).",
        )
        parser.add_argument(
            "--tage",
            type=int,
            default=None,
            help="Anzahl der Tage im Voraus, die synchronisiert werden sollen.",
        )

    def handle(self, *args, **options):
        spezifisch = options.get("fahrlehrer")
        tage = options.get("tage")

        if spezifisch:
            try:
                if spezifisch.isdigit():
                    fahrlehrer = Fahrlehrer.objects.get(pk=int(spezifisch))
                else:
                    fahrlehrer = Fahrlehrer.objects.get(slug=spezifisch)
            except Fahrlehrer.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Fahrlehrer '{spezifisch}' nicht gefunden."))
                return

            self.stdout.write(f"Synchronisiere FSM für {fahrlehrer.name}...")
            anzahl = sync_blocker_fuer_fahrlehrer(fahrlehrer, tage_voraus=tage)
            self.stdout.write(
                self.style.SUCCESS(f"{anzahl} aktive Sperrzeiten für {fahrlehrer.name} synchronisiert.")
            )
        else:
            self.stdout.write("Synchronisiere FSM für alle aktiven Fahrlehrer...")
            ergebnisse = sync_alle_fahrlehrer()
            gesamt = sum(ergebnisse.values())
            self.stdout.write(
                self.style.SUCCESS(
                    f"Fertig: {gesamt} Sperrzeiten für {len(ergebnisse)} Fahrlehrer synchronisiert."
                )
            )
