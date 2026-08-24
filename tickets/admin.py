"""Admin screens for the desk. Ticket edits go through the service layer."""

from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from tickets import services
from tickets.models import (
    AuditEvent,
    Comment,
    SlaPolicy,
    Status,
    Team,
    TeamMembership,
    Ticket,
)


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 1
    autocomplete_fields = ("user",)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "member_count")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug")
    inlines = [TeamMembershipInline]

    @admin.display(description="members")
    def member_count(self, team):
        return team.memberships.count()


@admin.register(SlaPolicy)
class SlaPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "team",
        "priority",
        "first_response_minutes",
        "resolution_minutes",
        "business_hours_only",
    )
    list_filter = ("priority", "business_hours_only", "team")


class CommentInline(admin.TabularInline):
    model = Comment
    extra = 0
    fields = ("author", "body", "is_internal", "created_at")
    readonly_fields = ("created_at",)


class AuditEventInline(admin.TabularInline):
    """History is written by the service layer, never typed in here."""

    model = AuditEvent
    extra = 0
    fields = ("created_at", "actor", "verb", "payload")
    readonly_fields = fields
    can_delete = False
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "title",
        "status",
        "priority",
        "team",
        "assignee",
        "sla_state",
        "created_at",
    )
    list_filter = ("status", "priority", "team", "escalation_level")
    search_fields = ("reference", "title", "description", "requester__username")
    autocomplete_fields = ("requester", "assignee")
    date_hierarchy = "created_at"
    inlines = [CommentInline, AuditEventInline]
    readonly_fields = (
        "reference",
        "sla_policy",
        "first_response_due_at",
        "resolution_due_at",
        "first_responded_at",
        "resolved_at",
        "closed_at",
        "first_response_breached_at",
        "resolution_breached_at",
        "escalation_level",
        "sla_paused_minutes",
        "pending_since",
        "created_at",
        "updated_at",
    )
    actions = ("mark_in_progress", "mark_resolved")

    @admin.display(description="SLA")
    def sla_state(self, ticket):
        if ticket.resolution_breached or ticket.first_response_breached:
            return format_html('<b style="color:#b91c1c">breached</b>')
        if ticket.resolution_due_at and ticket.is_open:
            if ticket.resolution_due_at < timezone.now():
                return format_html('<span style="color:#b45309">due</span>')
        return "ok"

    def _bulk_transition(self, request, queryset, target):
        moved, blocked = 0, 0
        for ticket in queryset:
            try:
                services.transition(ticket, target, actor=request.user)
                moved += 1
            except services.InvalidTransition:
                blocked += 1
        self.message_user(request, f"{moved} moved, {blocked} not allowed", messages.INFO)

    @admin.action(description="Move selected tickets to In progress")
    def mark_in_progress(self, request, queryset):
        self._bulk_transition(request, queryset, Status.IN_PROGRESS)

    @admin.action(description="Move selected tickets to Resolved")
    def mark_resolved(self, request, queryset):
        self._bulk_transition(request, queryset, Status.RESOLVED)


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "ticket", "verb", "actor")
    list_filter = ("verb",)
    search_fields = ("ticket__reference",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
