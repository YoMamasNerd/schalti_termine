from django.conf import settings
from django.urls import reverse

from .services.mail import get_kontakt_email


def site(request):
    """Stellt Seitenname und rechtliche Links jedem Template zur Verfügung."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_BASE_URL": settings.SITE_BASE_URL,
        "KONTAKT_EMAIL": get_kontakt_email(),
        "RESERVATION_MINUTES": settings.RESERVATION_MINUTES,
        "FSM_SYNC_ENABLED": getattr(settings, "FSM_SYNC_ENABLED", False),
        "VOIDAUTH_ENABLED": getattr(settings, "VOIDAUTH_ENABLED", False),
    }

