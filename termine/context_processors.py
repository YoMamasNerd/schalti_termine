from django.conf import settings


def site(request):
    """Stellt Seitenname und rechtliche Links jedem Template zur Verfügung."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_BASE_URL": settings.SITE_BASE_URL,
        "RESERVATION_MINUTES": settings.RESERVATION_MINUTES,
        "IMPRESSUM_URL": settings.IMPRESSUM_URL,
        "DATENSCHUTZ_URL": settings.DATENSCHUTZ_URL,
    }
