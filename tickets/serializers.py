"""Serializers for the ticket API."""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from tickets.models import (
    AuditEvent,
    Comment,
    Priority,
    SlaPolicy,
    Status,
    Team,
    Ticket,
)

User = get_user_model()


class UserBriefSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "name", "role")
        read_only_fields = fields


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ("id", "name", "slug")


class SlaPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = SlaPolicy
        fields = (
            "id",
            "name",
            "team",
            "priority",
            "first_response_minutes",
            "resolution_minutes",
            "business_hours_only",
        )


class CommentSerializer(serializers.ModelSerializer):
    author = UserBriefSerializer(read_only=True)

    class Meta:
        model = Comment
        fields = ("id", "author", "body", "is_internal", "created_at")
        read_only_fields = ("id", "author", "created_at")

    def validate_is_internal(self, value):
        """Only agents get to write notes the requester cannot see."""
        if value and not self.context["request"].user.is_agent:
            raise serializers.ValidationError("Only agents can write internal notes.")
        return value


class AuditEventSerializer(serializers.ModelSerializer):
    actor = UserBriefSerializer(read_only=True)

    class Meta:
        model = AuditEvent
        fields = ("id", "actor", "verb", "payload", "created_at")
        read_only_fields = fields


class TicketSerializer(serializers.ModelSerializer):
    requester = UserBriefSerializer(read_only=True)
    assignee = UserBriefSerializer(read_only=True)
    team = TeamSerializer(read_only=True)
    sla = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = (
            "id",
            "reference",
            "title",
            "description",
            "status",
            "priority",
            "requester",
            "assignee",
            "team",
            "escalation_level",
            "sla",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_sla(self, ticket) -> dict:
        return {
            "policy": ticket.sla_policy.name if ticket.sla_policy else None,
            "first_response_due_at": ticket.first_response_due_at,
            "first_responded_at": ticket.first_responded_at,
            "first_response_breached": ticket.first_response_breached,
            "resolution_due_at": ticket.resolution_due_at,
            "resolved_at": ticket.resolved_at,
            "resolution_breached": ticket.resolution_breached,
            "paused_minutes": ticket.sla_paused_minutes,
        }


class TicketDetailSerializer(TicketSerializer):
    comments = serializers.SerializerMethodField()
    events = AuditEventSerializer(many=True, read_only=True)

    class Meta(TicketSerializer.Meta):
        fields = (*TicketSerializer.Meta.fields, "comments", "events")
        read_only_fields = fields

    def get_comments(self, ticket) -> list:
        """Hide internal notes from the person who opened the ticket."""
        comments = ticket.comments.select_related("author")
        if not self.context["request"].user.is_agent:
            comments = comments.filter(is_internal=False)
        return CommentSerializer(comments, many=True, context=self.context).data


class TicketCreateSerializer(serializers.ModelSerializer):
    team = serializers.SlugRelatedField(
        slug_field="slug", queryset=Team.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = Ticket
        fields = ("title", "description", "team", "priority")


class TicketTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Status.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class TicketAssignSerializer(serializers.Serializer):
    assignee = serializers.SlugRelatedField(
        slug_field="username", queryset=User.objects.all(), allow_null=True
    )

    def validate_assignee(self, value):
        if value is not None and not value.is_agent:
            raise serializers.ValidationError("Tickets can only be assigned to agents.")
        return value


class TicketPrioritySerializer(serializers.Serializer):
    priority = serializers.ChoiceField(choices=Priority.choices)
