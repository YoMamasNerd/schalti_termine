from django.contrib import admin, messages
from django.utils.formats import date_format
from django.utils.html import format_html

from .forms import RhythmusRegelForm
from .models import Buchung, Fahrlehrer, RhythmusRegel, Sperrzeit, Termin, Terminart
from .services import buchung as buchungs_service
from .services.planung import generiere_termine

# Wer hier ankommt und noch keinen Superuser hat, kann sich nicht anmelden und
# erfährt sonst nirgends, warum. Die eigene Vorlage ergänzt die des Admins nur
# um den Einrichtungshinweis.
admin.site.login_template = "staff/admin_anmelden.html"


class RhythmusRegelInline(admin.TabularInline):
    model = RhythmusRegel
    form = RhythmusRegelForm
    extra = 0
    fields = (
        "terminart",
        "wochentage",
        "beginn",
        "ende",
        "intervall_wochen",
        "gueltig_ab",
        "gueltig_bis",
        "feiertage_auslassen",
        "aktiv",
    )


@admin.register(Fahrlehrer)
class FahrlehrerAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "bundesland", "horizont_wochen", "vorlauf_stunden", "aktiv")
    list_filter = ("aktiv", "bundesland")
    search_fields = ("name", "email")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("kalender_abo",)
    inlines = [RhythmusRegelInline]
    actions = ["termine_generieren", "feed_token_zuruecksetzen"]
    fieldsets = (
        (None, {"fields": ("name", "slug", "benutzer", "aktiv", "reihenfolge")}),
        ("Kontakt", {"fields": ("email", "telefon", "beschreibung")}),
        (
            "Terminplanung",
            {
                "fields": ("bundesland", "horizont_wochen", "vorlauf_stunden"),
                "description": "Das Bundesland bestimmt, welche gesetzlichen Feiertage "
                "die automatische Planung überspringt.",
            },
        ),
        ("Kalender-Abo", {"fields": ("kalender_abo",)}),
    )

    @admin.display(description="Abo-URL für Outlook/Google/Apple")
    def kalender_abo(self, obj):
        if not obj.pk:
            return "Wird nach dem Speichern erzeugt."
        return format_html(
            '<code>{}</code><br><small>Diese URL im Kalenderprogramm als Abo eintragen. '
            "Wer sie kennt, sieht alle Termine – also nicht weitergeben.</small>",
            obj.feed_url,
        )

    @admin.action(description="Termine aus Rhythmus-Regeln erzeugen")
    def termine_generieren(self, request, queryset):
        for fahrlehrer in queryset:
            bericht = generiere_termine(fahrlehrer)
            self.message_user(
                request, f"{fahrlehrer.name}: {bericht.als_text()}", messages.SUCCESS
            )

    @admin.action(description="Kalender-Abo-Token zurücksetzen (altes Abo wird ungültig)")
    def feed_token_zuruecksetzen(self, request, queryset):
        from .models import neuer_token

        for fahrlehrer in queryset:
            fahrlehrer.feed_token = neuer_token()
            fahrlehrer.save(update_fields=["feed_token"])
        self.message_user(request, f"{queryset.count()} Token zurückgesetzt.", messages.WARNING)


@admin.register(Terminart)
class TerminartAdmin(admin.ModelAdmin):
    list_display = ("name", "dauer_minuten", "puffer_minuten", "ort", "aktiv", "reihenfolge")
    list_filter = ("aktiv",)
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(RhythmusRegel)
class RhythmusRegelAdmin(admin.ModelAdmin):
    form = RhythmusRegelForm
    list_display = (
        "__str__",
        "fahrlehrer",
        "terminart",
        "rhythmus",
        "gueltig_ab",
        "gueltig_bis",
        "feiertage_auslassen",
        "aktiv",
    )
    list_filter = ("aktiv", "fahrlehrer", "terminart", "feiertage_auslassen")
    actions = ["termine_generieren"]

    @admin.display(description="Rhythmus")
    def rhythmus(self, obj):
        if obj.intervall_wochen == 1:
            return f"{obj.wochentage_kurz}, wöchentlich"
        return f"{obj.wochentage_kurz}, alle {obj.intervall_wochen} Wochen"

    @admin.action(description="Termine für die betroffenen Fahrlehrer neu erzeugen")
    def termine_generieren(self, request, queryset):
        for fahrlehrer in Fahrlehrer.objects.filter(
            pk__in=queryset.values_list("fahrlehrer_id", flat=True)
        ):
            bericht = generiere_termine(fahrlehrer)
            self.message_user(
                request, f"{fahrlehrer.name}: {bericht.als_text()}", messages.SUCCESS
            )


@admin.register(Sperrzeit)
class SperrzeitAdmin(admin.ModelAdmin):
    list_display = ("fahrlehrer", "beginn", "ende", "grund")
    list_filter = ("fahrlehrer",)
    date_hierarchy = "beginn"


class BuchungInline(admin.TabularInline):
    model = Buchung
    extra = 0
    fields = ("name", "email", "telefon", "status", "erstellt_am")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj):
        return False


@admin.register(Termin)
class TerminAdmin(admin.ModelAdmin):
    list_display = ("beginn_anzeige", "fahrlehrer", "terminart", "status", "herkunft", "kunde")
    list_filter = ("status", "herkunft", "fahrlehrer", "terminart")
    date_hierarchy = "beginn"
    search_fields = ("buchungen__name", "buchungen__email")
    autocomplete_fields = ("fahrlehrer",)
    inlines = [BuchungInline]
    list_select_related = ("fahrlehrer", "terminart")

    @admin.display(description="Beginn", ordering="beginn")
    def beginn_anzeige(self, obj):
        return date_format(obj.beginn_lokal, "D, j. F Y, H:i")

    @admin.display(description="Gebucht von")
    def kunde(self, obj):
        buchung = obj.aktive_buchung
        return buchung.name if buchung else "—"

    def delete_queryset(self, request, queryset):
        belegt = queryset.exclude(status=Termin.Status.FREI).count()
        if belegt:
            self.message_user(
                request,
                f"{belegt} Termine sind belegt und wurden nicht gelöscht.",
                messages.WARNING,
            )
        queryset.filter(status=Termin.Status.FREI).delete()


@admin.register(Buchung)
class BuchungAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "termin_anzeige", "fahrlehrer", "status", "erstellt_am")
    list_filter = ("status", "termin__fahrlehrer", "termin__terminart")
    search_fields = ("name", "email", "telefon", "referenz")
    date_hierarchy = "erstellt_am"
    readonly_fields = (
        "referenz",
        "token",
        "erstellt_am",
        "bestaetigt_am",
        "storniert_am",
        "erinnerung_am",
        "einwilligung_am",
        "anonymisiert_am",
        "links",
    )
    actions = ["absagen"]
    list_select_related = ("termin", "termin__fahrlehrer")

    @admin.display(description="Termin", ordering="termin__beginn")
    def termin_anzeige(self, obj):
        return date_format(obj.termin.beginn_lokal, "D, j. F Y, H:i")

    @admin.display(description="Fahrlehrer")
    def fahrlehrer(self, obj):
        return obj.termin.fahrlehrer

    @admin.display(description="Links")
    def links(self, obj):
        if not obj.pk:
            return "—"
        return format_html(
            'Bestätigung: <a href="{}">{}</a><br>Verwaltung: <a href="{}">{}</a>',
            obj.bestaetigungs_url,
            obj.bestaetigungs_url,
            obj.verwaltungs_url,
            obj.verwaltungs_url,
        )

    @admin.action(description="Buchung absagen und Kunden benachrichtigen")
    def absagen(self, request, queryset):
        anzahl = 0
        for buchung in queryset:
            if buchung.ist_aktiv:
                buchungs_service.stornieren(buchung, von="fahrschule")
                anzahl += 1
        self.message_user(request, f"{anzahl} Buchungen abgesagt.", messages.SUCCESS)
