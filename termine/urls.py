from django.contrib.auth import views as auth_views
from django.urls import path

from . import staff_views, views

app_name = "termine"

urlpatterns = [
    # --- Öffentliche Buchung ---
    path("", views.startseite, name="start"),
    path("fahrlehrer/<slug:slug>/", views.startseite, name="fahrlehrer"),
    path("buchen/<int:termin_id>/", views.buchen, name="buchen"),
    path("bestaetigen/<str:token>/", views.bestaetigen, name="bestaetigen"),
    path("termin/<str:token>/", views.buchung_ansicht, name="buchung"),
    path("termin/<str:token>/stornieren/", views.buchung_stornieren, name="buchung_stornieren"),
    path("termin/<str:token>/loeschen/", views.buchung_loeschen, name="buchung_loeschen"),
    path("kalender/<str:token>.ics", views.ics_feed, name="ics_feed"),
    path("impressum/", views.impressum, name="impressum"),
    path("datenschutz/", views.datenschutz, name="datenschutz"),
    # Nur die Terminauswahl, ohne Kopf und Fuß – für den Rahmen in einer
    # fremden Seite. Siehe docs/EINBETTEN.md.
    path("einbetten/", views.einbetten, name="einbetten"),
    # Ohne Schrägstrich am Ende: Der Container fragt diese Adresse alle paar
    # Sekunden ab, eine Umleitung wäre dabei nur unnötiger Verkehr.
    path("healthz", views.healthz, name="healthz"),
    # --- Interner Bereich ---
    path(
        "intern/anmelden/",
        auth_views.LoginView.as_view(template_name="staff/anmelden.html"),
        name="login",
    ),
    path("intern/abmelden/", auth_views.LogoutView.as_view(), name="logout"),
    path("intern/", staff_views.dashboard, name="dashboard"),
    path("intern/planung/", staff_views.tagesplanung, name="tagesplanung"),
    path("intern/planung/anlegen/", staff_views.termine_anlegen, name="termine_anlegen"),
    path("intern/planung/sperrzeit/", staff_views.sperrzeit_anlegen, name="sperrzeit_anlegen"),
    path("intern/planung/generieren/", staff_views.generieren, name="generieren"),
    path("intern/termin/<int:pk>/loeschen/", staff_views.termin_loeschen, name="termin_loeschen"),
    path("intern/buchungen/", staff_views.buchungsliste, name="buchungen"),
    path("intern/buchungen/<int:pk>/", staff_views.buchung_detail, name="buchung_detail"),
    path(
        "intern/buchungen/<int:pk>/verschieben/",
        staff_views.buchung_verschieben,
        name="buchung_verschieben",
    ),
    path(
        "intern/buchungen/<int:pk>/stornieren/",
        staff_views.buchung_absagen,
        name="buchung_absagen",
    ),
    path("intern/regeln/", staff_views.regelliste, name="regeln"),
    path("intern/regeln/neu/", staff_views.regel_bearbeiten, name="regel_neu"),
    path("intern/regeln/<int:pk>/", staff_views.regel_bearbeiten, name="regel_bearbeiten"),
    path("intern/regeln/<int:pk>/loeschen/", staff_views.regel_loeschen, name="regel_loeschen"),
    path("intern/terminarten/", staff_views.terminartenliste, name="terminarten"),
    path("intern/terminarten/neu/", staff_views.terminart_bearbeiten, name="terminart_neu"),
    path(
        "intern/terminarten/<int:pk>/",
        staff_views.terminart_bearbeiten,
        name="terminart_bearbeiten",
    ),
    path(
        "intern/terminarten/<int:pk>/loeschen/",
        staff_views.terminart_loeschen,
        name="terminart_loeschen",
    ),
    path("intern/klassen/", staff_views.klassenliste, name="klassen"),
    path("intern/klassen/neu/", staff_views.klasse_bearbeiten, name="klasse_neu"),
    path("intern/klassen/<int:pk>/", staff_views.klasse_bearbeiten, name="klasse_bearbeiten"),
    path("intern/klassen/<int:pk>/loeschen/", staff_views.klasse_loeschen, name="klasse_loeschen"),
    path("intern/einstellungen/", staff_views.einstellungen, name="einstellungen"),
    path(
        "intern/einstellungen/kalender-abo/",
        staff_views.feed_token_neu,
        name="feed_token_neu",
    ),
    path(
        "intern/einstellungen/sperrzeit/<int:pk>/loeschen/",
        staff_views.sperrzeit_loeschen,
        name="sperrzeit_loeschen",
    ),
    path("intern/einstellungen/fahrlehrer/neu/", staff_views.fahrlehrer_neu, name="fahrlehrer_neu"),
    path("intern/einstellungen/smtp-test/", staff_views.smtp_test_ajax, name="smtp_test"),
    path("intern/einstellungen/fsm/", staff_views.fsm_einstellungen, name="fsm_einstellungen"),
]

