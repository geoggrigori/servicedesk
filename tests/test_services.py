"""Rules that live in the service layer: status machine, clock, escalation."""

import pytest
from freezegun import freeze_time

from tickets import services
from tickets.models import AuditVerb, Priority, Status

pytestmark = pytest.mark.django_db


def open_ticket(requester, team, priority=Priority.NORMAL):
    return services.create_ticket(
        title="VPN keeps dropping", requester=requester, team=team, priority=priority
    )


# 2026-03-03 13:00 UTC is 10:00 in America/Sao_Paulo, a Tuesday morning.
MORNING = "2026-03-03 13:00:00"


@freeze_time(MORNING)
def test_new_ticket_gets_a_reference_and_deadlines(requester, team, policy):
    ticket = open_ticket(requester, team)

    assert ticket.reference.startswith("TCK-")
    assert ticket.sla_policy == policy
    # 60 working minutes after 10:00, and 480 which spills into the next day.
    assert ticket.first_response_due_at.astimezone(ticket.created_at.tzinfo) is not None
    assert ticket.first_response_due_at > ticket.created_at
    assert ticket.resolution_due_at > ticket.first_response_due_at
    assert ticket.events.filter(verb=AuditVerb.CREATED).exists()


@freeze_time(MORNING)
def test_a_ticket_without_a_matching_policy_has_no_deadlines(requester, team):
    ticket = open_ticket(requester, team, priority=Priority.LOW)

    assert ticket.sla_policy is None
    assert ticket.resolution_due_at is None


@freeze_time(MORNING)
def test_global_policy_is_the_fallback_when_the_team_has_none(requester, team, db):
    from tickets.models import SlaPolicy

    SlaPolicy.objects.create(
        name="Global low",
        team=None,
        priority=Priority.LOW,
        first_response_minutes=480,
        resolution_minutes=2880,
    )
    ticket = open_ticket(requester, team, priority=Priority.LOW)

    assert ticket.sla_policy.name == "Global low"


@freeze_time(MORNING)
def test_team_policy_beats_the_global_one(requester, team, policy, db):
    from tickets.models import SlaPolicy

    SlaPolicy.objects.create(
        name="Global normal",
        team=None,
        priority=Priority.NORMAL,
        first_response_minutes=999,
        resolution_minutes=9999,
    )
    ticket = open_ticket(requester, team)

    assert ticket.sla_policy == policy


def test_illegal_transitions_are_refused(requester, team, policy, agent):
    ticket = open_ticket(requester, team)
    services.transition(ticket, Status.CANCELLED, actor=agent)

    with pytest.raises(services.InvalidTransition):
        services.transition(ticket, Status.IN_PROGRESS, actor=agent)


def test_transition_to_the_same_status_is_a_no_op(requester, team, policy, agent):
    ticket = open_ticket(requester, team)
    before = ticket.events.count()

    services.transition(ticket, Status.NEW, actor=agent)

    assert ticket.events.count() == before


def test_resolving_then_closing_stamps_both_times(requester, team, policy, agent):
    ticket = open_ticket(requester, team)
    services.transition(ticket, Status.RESOLVED, actor=agent)
    resolved_at = ticket.resolved_at

    services.transition(ticket, Status.CLOSED, actor=agent)

    assert ticket.resolved_at == resolved_at
    assert ticket.closed_at is not None


def test_reopening_clears_the_outcome_stamps(requester, team, policy, agent):
    ticket = open_ticket(requester, team)
    services.transition(ticket, Status.RESOLVED, actor=agent)
    services.transition(ticket, Status.CLOSED, actor=agent)

    services.transition(ticket, Status.IN_PROGRESS, actor=agent)

    assert ticket.resolved_at is None
    assert ticket.closed_at is None
    assert ticket.status == Status.IN_PROGRESS


def test_waiting_on_the_requester_pauses_the_resolution_clock(requester, team, policy, agent):
    with freeze_time(MORNING) as clock:
        ticket = open_ticket(requester, team)
        original_due = ticket.resolution_due_at

        services.transition(ticket, Status.PENDING, actor=agent)
        clock.tick(60 * 60 * 2)  # two working hours pass
        services.transition(ticket, Status.IN_PROGRESS, actor=agent)

    ticket.refresh_from_db()
    assert ticket.sla_paused_minutes == 120
    assert ticket.resolution_due_at > original_due
    assert ticket.pending_since is None
    assert ticket.events.filter(verb=AuditVerb.SLA_RESUMED).exists()


def test_time_paused_outside_business_hours_is_not_credited(requester, team, policy, agent):
    # Pause at closing time and resume when the desk opens again: the night
    # in between holds no working minutes, so the deadline must not move.
    with freeze_time("2026-03-03 21:00:00") as clock:
        ticket = open_ticket(requester, team)
        services.transition(ticket, Status.PENDING, actor=agent)
        clock.move_to("2026-03-04 12:00:00")
        services.transition(ticket, Status.IN_PROGRESS, actor=agent)

    ticket.refresh_from_db()
    assert ticket.sla_paused_minutes == 0


def test_first_public_agent_reply_stops_the_response_clock(requester, team, policy, agent):
    ticket = open_ticket(requester, team)

    services.add_comment(ticket, agent, "On it.")

    ticket.refresh_from_db()
    assert ticket.first_responded_at is not None
    assert ticket.events.filter(verb=AuditVerb.FIRST_RESPONSE).count() == 1


def test_internal_notes_do_not_count_as_a_response(requester, team, policy, agent):
    ticket = open_ticket(requester, team)

    services.add_comment(ticket, agent, "Checking the firewall logs.", is_internal=True)

    ticket.refresh_from_db()
    assert ticket.first_responded_at is None


def test_the_requester_talking_to_themselves_is_not_a_response(requester, team, policy):
    ticket = open_ticket(requester, team)

    services.add_comment(ticket, requester, "Any update?")

    ticket.refresh_from_db()
    assert ticket.first_responded_at is None


def test_only_the_first_reply_stops_the_clock(requester, team, policy, agent):
    ticket = open_ticket(requester, team)
    services.add_comment(ticket, agent, "On it.")
    ticket.refresh_from_db()
    first = ticket.first_responded_at

    services.add_comment(ticket, agent, "Still on it.")

    ticket.refresh_from_db()
    assert ticket.first_responded_at == first


@freeze_time(MORNING)
def test_raising_priority_tightens_the_deadlines(requester, team, policy, urgent_policy, agent):
    ticket = open_ticket(requester, team)
    normal_due = ticket.resolution_due_at

    services.change_priority(ticket, Priority.URGENT, actor=agent)

    ticket.refresh_from_db()
    assert ticket.sla_policy == urgent_policy
    assert ticket.resolution_due_at < normal_due
    assert ticket.events.filter(verb=AuditVerb.PRIORITY_CHANGED).exists()


def test_escalation_hands_the_ticket_to_the_team_lead(requester, team, policy, lead):
    ticket = open_ticket(requester, team)

    services.escalate(ticket, reason="resolution SLA breached")

    ticket.refresh_from_db()
    assert ticket.escalation_level == 1
    assert ticket.assignee == lead
    event = ticket.events.filter(verb=AuditVerb.ESCALATED).get()
    assert event.payload["reason"] == "resolution SLA breached"
    assert event.actor is None  # the system acted, not a person


def test_assigning_records_both_sides_of_the_handover(requester, team, policy, agent, lead):
    ticket = open_ticket(requester, team)
    services.assign(ticket, agent, actor=agent)

    services.assign(ticket, lead, actor=agent)

    event = ticket.events.filter(verb=AuditVerb.ASSIGNED).last()
    assert event.payload["from"] == agent.username
    assert event.payload["to"] == lead.username
