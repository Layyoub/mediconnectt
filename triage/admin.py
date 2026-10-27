from django.contrib import admin

from .models import RedFlagRule, SymptomEntry, TriageResult, TriageSession


class SymptomEntryInline(admin.TabularInline):
    model = SymptomEntry
    extra = 0


@admin.register(TriageSession)
class TriageSessionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "status", "age", "temperature", "created_at")
    list_filter = ("status",)
    search_fields = ("patient__first_name", "patient__last_name", "patient__email", "chief_complaint")
    inlines = [SymptomEntryInline]


@admin.register(TriageResult)
class TriageResultAdmin(admin.ModelAdmin):
    list_display = ("session", "level", "score", "suggested_specialty", "created_at")
    list_filter = ("level", "suggested_specialty")
    filter_horizontal = ("red_flags",)


@admin.register(RedFlagRule)
class RedFlagRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "symptom", "min_severity", "combined_symptom", "min_temperature", "level", "is_active")
    list_filter = ("level", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name", "message")
