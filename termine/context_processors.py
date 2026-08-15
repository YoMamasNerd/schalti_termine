from django.conf import settings
from django.urls import reverse


def site(request):
    """Stellt Seitenname und rechtliche Links jedem Template zur Verfügung."""
    kontakt_email = getattr(settings, "DEFAULT_FROM_EMAIL", "mail@fahrschule-schaltwerk.de")
    if "<" in kontakt_email and ">" in kontakt_email:
        kontakt_email = kontakt_email.split("<")[1].replace(">", "").strip()

    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_BASE_URL": settings.SITE_BASE_URL,
        "KONTAKT_EMAIL": kontakt_email,
        "RESERVATION_MINUTES": settings.RESERVATION_MINUTES,
        "IMPRESSUM_URL": settings.IMPRESSUM_URL,
        "DATENSCHUTZ_URL": settings.DATENSCHUTZ_URL,
        "FSM_SYNC_ENABLED": getattr(settings, "FSM_SYNC_ENABLED", False),
    }
