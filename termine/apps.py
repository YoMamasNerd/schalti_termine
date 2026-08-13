from django.apps import AppConfig


class TermineConfig(AppConfig):
    """Konfiguration der einzigen fachlichen App dieses Projekts.

    Bewusst keine Signale: Der Ablauf einer Buchung steht vollständig in
    `services/buchung.py` und wird von dort aus explizit aufgerufen. Signale
    würden dieselbe Logik über das Projekt verteilen und die Reihenfolge der
    Nebenwirkungen verschleiern.

    `ready()` lädt stattdessen die Systemprüfungen, die vor dem Start warnen,
    wenn die Konfiguration Buchungen unbrauchbar machen würde.
    """

    name = "termine"
    verbose_name = "Beratungstermine"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import checks  # noqa: F401  (Registrierung per Dekorator)
