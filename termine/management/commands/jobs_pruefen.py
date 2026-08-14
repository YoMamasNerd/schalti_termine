from django.core.management.base import BaseCommand

from ...services.zustand import TOLERANZ_MINUTEN, jobs


class Command(BaseCommand):
    help = (
        "Prüft, ob der Fahrplan der Hintergrundjobs weiterläuft. "
        "Endet mit 1, wenn nicht – gedacht als Healthcheck des Worker-Containers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--toleranz",
            type=int,
            default=TOLERANZ_MINUTEN,
            metavar="MINUTEN",
            help=f"Wie weit ein Job zurückliegen darf (Vorgabe: {TOLERANZ_MINUTEN}).",
        )

    def handle(self, *args, **optionen):
        befund = jobs(toleranz_minuten=optionen["toleranz"])
        if befund.gesund:
            self.stdout.write(self.style.SUCCESS(befund.meldung))
            return
        # Kein CommandError: Der Healthcheck will einen Rückgabewert, keinen
        # Stacktrace im Containerprotokoll.
        self.stderr.write(befund.meldung)
        raise SystemExit(1)
