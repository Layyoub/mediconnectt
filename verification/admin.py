from django.contrib import admin

from .models import DoctorVerification


@admin.register(DoctorVerification)
class DoctorVerificationAdmin(admin.ModelAdmin):
    list_display = ("doctor", "status", "risk", "recommendation", "ai_used", "created_at")
    list_filter = ("status", "risk", "ai_used")
    raw_id_fields = ("doctor", "triggered_by")
    readonly_fields = ("checks", "created_at", "finished_at")
