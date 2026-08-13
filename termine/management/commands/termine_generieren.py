import datetime as dt

from django.core.management.base import BaseCommand, CommandError

from termine.models import Fahrlehrer
from termine.services.planung import generiere_alle, generiere_termine


class Command(BaseCommand):
    help = (
        "Rollt die Rhythmus-Regeln in konkrete Termine aus. "
        "Läuft idempotent und fasst gebuchte Termine nie an."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fahrlehrer",
            help="Nur für diesen Fahrlehrer (URL-Kürzel). Ohne Angabe: alle aktiven.",
        )
        parser.add_argument(
            "--wochen",
            type=int,
            help="Planungshorizont überschreiben. Ohne Angabe gilt der Wert beim Fahrlehrer.",
        )
        parser.add_argument(
            "--ab", help="Startdatum im Format JJJJ-MM-TT. Ohne Angabe: heute."
        )

    def handle(self, *args, **optionen):
        ab = None
        if optionen.get("ab"):
            try:
                ab = dt.date.fromisoformat(optionen["ab"])
            except ValueError as exc:
                raise CommandError("--ab erwartet das Format JJJJ-MM-TT.") from exc

        if optionen.get("fahrlehrer"):
            fahrlehrer = Fahrlehrer.objects.filter(slug=optionen["fahrlehrer"]).first()
            if fahrlehrer is None:
                raise CommandError(f"Kein Fahrlehrer mit dem Kürzel {optionen['fahrlehrer']!r}.")
            bericht = generiere_termine(fahrlehrer, wochen=optionen.get("wochen"), ab=ab)
        else:
            bericht = generiere_alle(wochen=optionen.get("wochen"), ab=ab)

        self.stdout.write(self.style.SUCCESS(bericht.als_text()))
