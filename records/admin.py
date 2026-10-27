from django.contrib import admin

from .models import (
    AccessLog, ConsultationNote, Contraindication, DoseLimit, Drug, DrugInteraction, MedicalDocument,
    MedicalRecord, Prescription, PrescriptionCheck, PrescriptionItem,
)


@admin.register(MedicalRecord)
class MedicalRecordAdmin(admin.ModelAdmin):
    list_display = ("patient", "blood_type", "updated_at")
    search_fields = ("patient__first_name", "patient__last_name", "patient__email")
    raw_id_fields = ("patient", "updated_by")


@admin.register(ConsultationNote)
class ConsultationNoteAdmin(admin.ModelAdmin):
    list_display = ("title", "record", "author", "visible_to_patient", "created_at")
    list_filter = ("visible_to_patient",)
    raw_id_fields = ("record", "author", "appointment")


@admin.register(Drug)
class DrugAdmin(admin.ModelAdmin):
    list_display = ("name", "strength", "form", "dci", "drug_class", "is_active")
    list_filter = ("form", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name", "dci", "drug_class")


class PrescriptionItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 0
    autocomplete_fields = ("drug",)


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("code", "record", "doctor", "created_at", "valid_until")
    search_fields = ("code",)
    raw_id_fields = ("record", "doctor", "appointment")
    inlines = [PrescriptionItemInline]


@admin.register(MedicalDocument)
class MedicalDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "record", "uploaded_by", "created_at")
    list_filter = ("kind",)
    raw_id_fields = ("record", "uploaded_by")


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    """Read-only: the journal must not be altered."""

    list_display = ("created_at", "record", "user", "action", "detail", "ip_address")
    list_filter = ("action",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DrugInteraction)
class DrugInteractionAdmin(admin.ModelAdmin):
    list_display = ("group_a", "group_b", "severity")
    list_filter = ("severity",)
    search_fields = ("group_a", "group_b", "advice")


@admin.register(DoseLimit)
class DoseLimitAdmin(admin.ModelAdmin):
    list_display = ("dci", "max_daily_mg", "per_kg_daily_mg", "min_age_months", "max_age_months")
    search_fields = ("dci", "note")


@admin.register(Contraindication)
class ContraindicationAdmin(admin.ModelAdmin):
    list_display = ("group", "severity", "min_age_months", "max_age_months")
    list_filter = ("severity",)
    search_fields = ("group", "message")


@admin.register(PrescriptionCheck)
class PrescriptionCheckAdmin(admin.ModelAdmin):
    list_display = ("created_at", "record", "doctor", "prescription", "max_severity", "ai_status")
    list_filter = ("max_severity", "ai_status")
    search_fields = ("record__patient__first_name", "record__patient__last_name", "prescription__code")
    readonly_fields = ("created_at", "findings", "ai_opinion")
