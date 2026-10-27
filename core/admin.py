from django.contrib import admin

from .models import DoctorProfile, PatientProfile


@admin.register(PatientProfile)
class PatientProfileAdmin(admin.ModelAdmin):
    list_display = ("__str__", "phone", "date_of_birth", "created_at")
    search_fields = ("user__first_name", "user__last_name", "user__email", "phone")


@admin.register(DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    list_display = ("__str__", "specialty", "license_number", "city", "status", "submitted_at")
    list_filter = ("status", "specialty")
    search_fields = ("user__first_name", "user__last_name", "user__email", "license_number")
