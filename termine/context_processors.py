from django.conf import settings

from .models.einstellungen import FahrschulEinstellungen
from .services.mail import get_kontakt_email


def site(request):
    """Stellt Seitenname und rechtliche Links jedem Template zur Verfügung."""
    try:
        reservierung_minuten = FahrschulEinstellungen.get_solo().reservierungsdauer_minuten
    except Exception:
        reservierung_minuten = settings.RESERVATION_MINUTES
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_BASE_URL": settings.SITE_BASE_URL,
        "KONTAKT_EMAIL": get_kontakt_email(),
        "RESERVATION_MINUTES": reservierung_minuten,
        "FSM_SYNC_ENABLED": getattr(settings, "FSM_SYNC_ENABLED", False),
        "VOIDAUTH_ENABLED": getattr(settings, "VOIDAUTH_ENABLED", False),
    }

