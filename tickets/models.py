"""Domain models for teams, SLA policies, tickets and their audit trail."""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


class Priority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class Status(models.TextChoices):
    NEW = "new", "New"
    IN_PROGRESS = "in_progress", "In progress"
    PENDING = "pending", "Pending requester"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


#: Statuses that stop the resolution clock for good.
TERMINAL_STATUSES = {Status.RESOLVED, Status.CLOSED, Status.CANCELLED}

#: Status the SLA clock pauses in, because the ball is with the requester.
PAUSED_STATUS = Status.PENDING


class Team(models.Model):
    """A queue tickets are routed to."""

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="TeamMembership",
        related_name="teams",
        blank=True,
    )

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class TeamMembership(models.Model):
    """Membership row, so we can flag who leads the queue."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    is_lead = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("team", "user"), name="unique_team_member"),
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.team}"


class SlaPolicy(models.Model):
    """Response and resolution targets for one priority on one team."""

    name = models.CharField(max_length=80)
    team = models.ForeignKey(
        Team, on_delete=models.CASCADE, related_name="sla_policies", null=True, blank=True
    )
    priority = models.CharField(max_length=16, choices=Priority.choices)
    first_response_minutes = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    resolution_minutes = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    business_hours_only = models.BooleanField(
        default=True,
        help_text="Count only working hours, instead of wall clock time.",
    )

    class Meta:
        verbose_name = "SLA policy"
        verbose_name_plural = "SLA policies"
        ordering = ("team__name", "priority")
        constraints = [
            models.UniqueConstraint(
                fields=("team", "priority"), name="unique_policy_per_team_priority"
            ),
        ]

    def __str__(self) -> str:
        scope = self.team.name if self.team else "global"
        return f"{scope} / {self.get_priority_display()}"

    @classmethod
    def resolve_for(cls, team: Team | None, priority: str) -> SlaPolicy | None:
        """Team policy wins; a global policy is the fallback."""
        return (
            cls.objects.filter(priority=priority)
            .filter(models.Q(team=team) | models.Q(team__isnull=True))
            .order_by(models.F("team").desc(nulls_last=True))
            .first()
        )


class TicketQuerySet(models.QuerySet):
    def open(self):
        return self.exclude(status__in=TERMINAL_STATUSES)

    def visible_to(self, user):
        """Requesters see their own tickets, agents see their queues.

        Membership is checked with EXISTS rather than a join. A join against
        a multi-valued relation returns one row per membership, which silently
        doubles counts in any aggregate built on top of this queryset.
        """
        if user.is_admin:
            return self
        if user.is_agent:
            member_of_team = TeamMembership.objects.filter(team=models.OuterRef("team"), user=user)
            return self.filter(
                models.Exists(member_of_team) | models.Q(assignee=user) | models.Q(requester=user)
            )
        return self.filter(requester=user)

    def breaching_first_response(self, now=None):
        now = now or timezone.now()
        return self.filter(
            first_responded_at__isnull=True,
            first_response_breached_at__isnull=True,
            first_response_due_at__lt=now,
        ).exclude(status__in=TERMINAL_STATUSES)

    def breaching_resolution(self, now=None):
        now = now or timezone.now()
        return self.filter(
            resolution_breached_at__isnull=True,
            resolution_due_at__lt=now,
        ).exclude(status__in=TERMINAL_STATUSES)


class Ticket(models.Model):
    reference = models.CharField(max_length=16, unique=True, editable=False, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_tickets"
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assigned_tickets",
        null=True,
        blank=True,
    )
    team = models.ForeignKey(
        Team, on_delete=models.PROTECT, related_name="tickets", null=True, blank=True
    )

    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)

    sla_policy = models.ForeignKey(
        SlaPolicy, on_delete=models.SET_NULL, related_name="tickets", null=True, blank=True
    )
    first_response_due_at = models.DateTimeField(null=True, blank=True)
    resolution_due_at = models.DateTimeField(null=True, blank=True)
    first_responded_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    first_response_breached_at = models.DateTimeField(null=True, blank=True)
    resolution_breached_at = models.DateTimeField(null=True, blank=True)
    escalation_level = models.PositiveSmallIntegerField(default=0)

    pending_since = models.DateTimeField(null=True, blank=True)
    sla_paused_minutes = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TicketQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status", "resolution_due_at")),
            models.Index(fields=("team", "status")),
            models.Index(fields=("assignee", "status")),
        ]

    def __str__(self) -> str:
        return f"{self.reference} {self.title}"

    def save(self, *args, **kwargs):
        """Assign a human readable reference right after the first insert."""
        creating = self._state.adding and not self.reference
        if creating:
            with transaction.atomic():
                super().save(*args, **kwargs)
                self.reference = f"TCK-{self.pk:05d}"
                super().save(update_fields=["reference"])
            return
        super().save(*args, **kwargs)

    @property
    def is_open(self) -> bool:
        return self.status not in TERMINAL_STATUSES

    @property
    def first_response_breached(self) -> bool:
        return self.first_response_breached_at is not None

    @property
    def resolution_breached(self) -> bool:
        return self.resolution_breached_at is not None


class Comment(models.Model):
    """A reply on a ticket. Internal notes stay hidden from the requester."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="comments"
    )
    body = models.TextField()
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [models.Index(fields=("ticket", "created_at"))]

    def __str__(self) -> str:
        return f"comment on {self.ticket.reference} by {self.author}"


class AuditVerb(models.TextChoices):
    CREATED = "created", "Created"
    STATUS_CHANGED = "status_changed", "Status changed"
    ASSIGNED = "assigned", "Assigned"
    PRIORITY_CHANGED = "priority_changed", "Priority changed"
    COMMENTED = "commented", "Commented"
    FIRST_RESPONSE = "first_response", "First response recorded"
    SLA_BREACHED = "sla_breached", "SLA breached"
    ESCALATED = "escalated", "Escalated"
    SLA_PAUSED = "sla_paused", "SLA paused"
    SLA_RESUMED = "sla_resumed", "SLA resumed"


class AuditEvent(models.Model):
    """Append-only history. Nothing here is ever edited after the fact."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        null=True,
        blank=True,
        help_text="Null when the system acted on its own.",
    )
    verb = models.CharField(max_length=32, choices=AuditVerb.choices)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [models.Index(fields=("ticket", "created_at"))]

    def __str__(self) -> str:
        return f"{self.ticket.reference} {self.verb}"
