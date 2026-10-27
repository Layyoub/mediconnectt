from django.contrib import admin

from .models import ChatMessage, ConsultationRoom, ConsultationSummary, Notification


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ("sender", "kind", "body", "created_at")
    can_delete = False


@admin.register(ConsultationRoom)
class ConsultationRoomAdmin(admin.ModelAdmin):
    list_display = ("appointment", "status", "opened_at", "closed_at")
    list_filter = ("status",)
    readonly_fields = ("code",)
    raw_id_fields = ("appointment",)
    inlines = [ChatMessageInline]


@admin.register(ConsultationSummary)
class ConsultationSummaryAdmin(admin.ModelAdmin):
    list_display = ("room", "author", "created_at")
    raw_id_fields = ("room", "author")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "title", "is_read", "created_at")
    list_filter = ("kind", "is_read")
    raw_id_fields = ("user",)
