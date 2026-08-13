import os

from django.conf import settings


def site(request):
    """Stellt Seitenname und rechtliche Links jedem Template zur Verfügung."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_BASE_URL": settings.SITE_BASE_URL,
        "RESERVATION_MINUTES": settings.RESERVATION_MINUTES,
        "IMPRESSUM_URL": os.environ.get("IMPRESSUM_URL", ""),
        "DATENSCHUTZ_URL": os.environ.get("DATENSCHUTZ_URL", ""),
    }
