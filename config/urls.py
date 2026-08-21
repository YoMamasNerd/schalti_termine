from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Schalti Termine – Verwaltung"
admin.site.site_title = "Schalti Termine"
admin.site.index_title = "Verwaltung"
admin.site.has_permission = lambda request: bool(
    request.user and request.user.is_active and request.user.is_superuser
)

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", include("termine.urls")),
]
