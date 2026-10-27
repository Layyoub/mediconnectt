from django.contrib import admin

from .models import Appointment, Availability, Specialty


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "icon", "position", "is_active")
    list_editable = ("position", "is_active")


@admin.register(Availability)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = ("doctor", "weekday", "start_time", "end_time", "slot_minutes", "is_active")
    list_filter = ("weekday", "is_active")
    search_fields = ("doctor__last_name", "doctor__email")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("start", "patient", "doctor", "specialty", "status")
    list_filter = ("status", "specialty")
    search_fields = ("patient__last_name", "doctor__last_name", "reason")
    date_hierarchy = "start"
    raw_id_fields = ("patient", "doctor", "triage_session", "cancelled_by")
