from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "MediConnect — Administration technique"
admin.site.site_title = "MediConnect"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("pharmacheck/", include("pharmacheck.urls")),
    path("", include("followups.urls")),
    path("triage/", include("triage.urls")),
    path("", include("appointments.urls")),
    path("", include("teleconsult.urls")),
    path("", include("records.urls")),
    path("", include("core.urls")),
]
