from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Schalti Termine – Verwaltung"
admin.site.site_title = "Schalti Termine"
admin.site.index_title = "Verwaltung"

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("termine.urls")),
]
