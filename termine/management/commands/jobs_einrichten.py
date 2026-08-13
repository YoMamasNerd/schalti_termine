from django.core.management.base import BaseCommand
from django.utils import timezone
from django_q.models import Schedule

# name -> (Funktionspfad, Schedule-Typ, Minuten, Beschreibung)
JOBS = {
    "Termine vorausplanen": (
        "termine.jobs.termine_vorausplanen",
        Schedule.DAILY,
        None,
        "Schiebt den Planungshorizont täglich weiter.",
    ),
    "Reservierungen aufräumen": (
        "termine.jobs.reservierungen_aufraeumen",
        Schedule.MINUTES,
        5,
        "Gibt nicht bestätigte Reservierungen wieder frei.",
    ),
    "Erinnerungen versenden": (
        "termine.jobs.erinnerungen",
        Schedule.HOURLY,
        None,
        "Verschickt Erinnerungsmails vor dem Termin.",
    ),
    "Datenpflege": (
        "termine.jobs.datenpflege",
        Schedule.DAILY,
        None,
        "Anonymisiert alte Buchungen (DSGVO).",
    ),
}


class Command(BaseCommand):
    help = "Legt die wiederkehrenden Hintergrundjobs in django-q2 an bzw. aktualisiert sie."

    def handle(self, *args, **optionen):
        jetzt = timezone.now()
        for name, (funktion, typ, minuten, beschreibung) in JOBS.items():
            plan, neu = Schedule.objects.update_or_create(
                name=name,
                defaults={
                    "func": funktion,
                    "schedule_type": typ,
                    "minutes": minuten,
                    "repeats": -1,
                    "next_run": jetzt,
                },
            )
            zustand = "angelegt" if neu else "aktualisiert"
            self.stdout.write(f"{name}: {zustand} – {beschreibung}")

        veraltet = Schedule.objects.filter(func__startswith="termine.jobs.").exclude(
            name__in=JOBS
        )
        if veraltet.exists():
            anzahl = veraltet.count()
            veraltet.delete()
            self.stdout.write(f"{anzahl} veraltete Jobs entfernt.")

        self.stdout.write(
            self.style.SUCCESS(
                "Fertig. Die Jobs laufen, sobald „python manage.py qcluster“ gestartet ist."
            )
        )
