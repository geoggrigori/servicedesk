"""Ticket operations.

Views and the admin both go through these functions, so the status machine,
the SLA clock and the audit trail can never drift apart.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from tickets.models import (
    PAUSED_STATUS,
    TERMINAL_STATUSES,
    AuditEvent,
    AuditVerb,
    Comment,
    Priority,
    SlaPolicy,
    Status,
    Team,
    Ticket,
)
from tickets.sla import BusinessCalendar, deadline_from, elapsed_from

#: Which status may follow which. Anything absent is rejected.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    Status.NEW: {Status.IN_PROGRESS, Status.PENDING, Status.RESOLVED, Status.CANCELLED},
    Status.IN_PROGRESS: {Status.PENDING, Status.RESOLVED, Status.CANCELLED},
    Status.PENDING: {Status.IN_PROGRESS, Status.RESOLVED, Status.CANCELLED},
    Status.RESOLVED: {Status.CLOSED, Status.IN_PROGRESS},
    Status.CLOSED: {Status.IN_PROGRESS},
    Status.CANCELLED: set(),
}


class InvalidTransition(Exception):
    """Raised when a status change is not allowed from the current status."""

    def __init__(self, current: str, target: str):
        self.current, self.target = current, target
        super().__init__(f"cannot move a ticket from {current} to {target}")


def record(ticket: Ticket, verb: str, actor=None, **payload) -> AuditEvent:
    """Append one entry to the ticket history."""
    return AuditEvent.objects.create(ticket=ticket, actor=actor, verb=verb, payload=payload)


def apply_sla(ticket: Ticket, *, anchor=None) -> None:
    """Attach the right policy and recompute both deadlines in memory."""
    policy = SlaPolicy.resolve_for(ticket.team, ticket.priority)
    ticket.sla_policy = policy
    if policy is None:
        ticket.first_response_due_at = None
        ticket.resolution_due_at = None
        return

    calendar = BusinessCalendar.from_settings()
    start = anchor or ticket.created_at or timezone.now()
    ticket.first_response_due_at = deadline_from(
        start, policy.first_response_minutes, calendar, policy.business_hours_only
    )
    ticket.resolution_due_at = deadline_from(
        start, policy.resolution_minutes, calendar, policy.business_hours_only
    )
    # Time already parked waiting on the requester still belongs to them.
    if ticket.sla_paused_minutes:
        ticket.resolution_due_at = deadline_from(
            ticket.resolution_due_at,
            ticket.sla_paused_minutes,
            calendar,
            policy.business_hours_only,
        )


@transaction.atomic
def create_ticket(
    *,
    title: str,
    requester,
    description: str = "",
    team: Team | None = None,
    priority: str = Priority.NORMAL,
    assignee=None,
    actor=None,
) -> Ticket:
    ticket = Ticket(
        title=title,
        description=description,
        requester=requester,
        team=team,
        priority=priority,
        assignee=assignee,
    )
    ticket.save()
    apply_sla(ticket)
    ticket.save(update_fields=["sla_policy", "first_response_due_at", "resolution_due_at"])
    record(
        ticket,
        AuditVerb.CREATED,
        actor=actor or requester,
        priority=ticket.priority,
        team=team.slug if team else None,
    )
    if assignee is not None:
        record(ticket, AuditVerb.ASSIGNED, actor=actor, to=assignee.username)
    return ticket


@transaction.atomic
def transition(ticket: Ticket, target: str, actor=None, *, note: str = "") -> Ticket:
    """Move a ticket to `target`, keeping SLA timestamps consistent."""
    current = ticket.status
    if target == current:
        return ticket
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidTransition(current, target)

    now = timezone.now()
    changed = ["status", "updated_at"]

    if current == PAUSED_STATUS:
        _resume_sla(ticket, now, actor, changed)
    if target == PAUSED_STATUS:
        ticket.pending_since = now
        changed.append("pending_since")
        record(ticket, AuditVerb.SLA_PAUSED, actor=actor)

    if target == Status.RESOLVED:
        ticket.resolved_at = now
        changed.append("resolved_at")
    if target == Status.CLOSED:
        ticket.closed_at = now
        ticket.resolved_at = ticket.resolved_at or now
        changed += ["closed_at", "resolved_at"]
    if current in TERMINAL_STATUSES and target not in TERMINAL_STATUSES:
        # Reopening clears the outcome stamps so reporting stays honest.
        ticket.resolved_at = None
        ticket.closed_at = None
        changed += ["resolved_at", "closed_at"]

    ticket.status = target
    ticket.save(update_fields=sorted(set(changed)))
    record(
        ticket,
        AuditVerb.STATUS_CHANGED,
        actor=actor,
        **{"from": current, "to": target, "note": note},
    )
    return ticket


def _resume_sla(ticket: Ticket, now, actor, changed: list[str]) -> None:
    """Give back the time the ticket spent waiting on the requester."""
    if ticket.pending_since is None:
        return
    policy = ticket.sla_policy
    calendar = BusinessCalendar.from_settings()
    business_hours_only = policy.business_hours_only if policy else True
    paused = elapsed_from(ticket.pending_since, now, calendar, business_hours_only)

    if paused and ticket.resolution_due_at:
        ticket.resolution_due_at = deadline_from(
            ticket.resolution_due_at, paused, calendar, business_hours_only
        )
        changed.append("resolution_due_at")

    ticket.sla_paused_minutes += paused
    ticket.pending_since = None
    changed += ["sla_paused_minutes", "pending_since"]
    record(ticket, AuditVerb.SLA_RESUMED, actor=actor, paused_minutes=paused)


@transaction.atomic
def assign(ticket: Ticket, assignee, actor=None) -> Ticket:
    previous = ticket.assignee
    if previous == assignee:
        return ticket
    ticket.assignee = assignee
    ticket.save(update_fields=["assignee", "updated_at"])
    record(
        ticket,
        AuditVerb.ASSIGNED,
        actor=actor,
        **{
            "from": previous.username if previous else None,
            "to": assignee.username if assignee else None,
        },
    )
    return ticket


@transaction.atomic
def change_priority(ticket: Ticket, priority: str, actor=None) -> Ticket:
    """Repricing a ticket also reprices its deadlines."""
    previous = ticket.priority
    if previous == priority:
        return ticket
    ticket.priority = priority
    apply_sla(ticket)
    ticket.save(
        update_fields=[
            "priority",
            "sla_policy",
            "first_response_due_at",
            "resolution_due_at",
            "updated_at",
        ]
    )
    record(
        ticket,
        AuditVerb.PRIORITY_CHANGED,
        actor=actor,
        **{"from": previous, "to": priority},
    )
    return ticket


@transaction.atomic
def add_comment(ticket: Ticket, author, body: str, *, is_internal: bool = False) -> Comment:
    """Post a reply. A public agent reply is what stops the response clock."""
    comment = Comment.objects.create(
        ticket=ticket, author=author, body=body, is_internal=is_internal
    )
    record(ticket, AuditVerb.COMMENTED, actor=author, internal=is_internal)

    counts_as_response = (
        not is_internal
        and author.is_agent
        and author != ticket.requester
        and ticket.first_responded_at is None
    )
    if counts_as_response:
        ticket.first_responded_at = comment.created_at
        ticket.save(update_fields=["first_responded_at", "updated_at"])
        record(ticket, AuditVerb.FIRST_RESPONSE, actor=author)

    return comment


def sweep_breaches(now=None) -> dict[str, int]:
    """Flag every ticket that has run past a deadline.

    Safe to run again at any moment: the `*_breached_at` columns filter out
    tickets that were already flagged, so a repeat sweep counts nothing twice.
    """
    now = now or timezone.now()
    counts = {"first_response": 0, "resolution": 0}

    for ticket in Ticket.objects.breaching_first_response(now).select_related("team"):
        ticket.first_response_breached_at = now
        ticket.save(update_fields=["first_response_breached_at", "updated_at"])
        record(
            ticket,
            AuditVerb.SLA_BREACHED,
            target="first_response",
            due_at=ticket.first_response_due_at.isoformat(),
        )
        counts["first_response"] += 1

    for ticket in Ticket.objects.breaching_resolution(now).select_related("team"):
        ticket.resolution_breached_at = now
        ticket.save(update_fields=["resolution_breached_at", "updated_at"])
        record(
            ticket,
            AuditVerb.SLA_BREACHED,
            target="resolution",
            due_at=ticket.resolution_due_at.isoformat(),
        )
        escalate(ticket, reason="resolution SLA breached")
        counts["resolution"] += 1

    return counts


@transaction.atomic
def escalate(ticket: Ticket, reason: str, actor=None) -> Ticket:
    """Bump the escalation level and hand the ticket to the team lead."""
    ticket.escalation_level += 1
    changed = ["escalation_level", "updated_at"]

    lead = None
    if ticket.team_id:
        membership = ticket.team.memberships.filter(is_lead=True).select_related("user").first()
        lead = membership.user if membership else None
    if lead and ticket.assignee_id != lead.pk:
        ticket.assignee = lead
        changed.append("assignee")

    ticket.save(update_fields=sorted(set(changed)))
    record(
        ticket,
        AuditVerb.ESCALATED,
        actor=actor,
        level=ticket.escalation_level,
        reason=reason,
        notified=lead.username if lead else None,
    )
    return ticket
