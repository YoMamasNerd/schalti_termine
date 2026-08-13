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
    path("kalender/<str:token>.ics", views.ics_feed, name="ics_feed"),
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
    path(
        "intern/buchungen/<int:pk>/stornieren/",
        staff_views.buchung_absagen,
        name="buchung_absagen",
    ),
    path("intern/regeln/", staff_views.regelliste, name="regeln"),
    path("intern/regeln/neu/", staff_views.regel_bearbeiten, name="regel_neu"),
    path("intern/regeln/<int:pk>/", staff_views.regel_bearbeiten, name="regel_bearbeiten"),
    path("intern/regeln/<int:pk>/loeschen/", staff_views.regel_loeschen, name="regel_loeschen"),
]
