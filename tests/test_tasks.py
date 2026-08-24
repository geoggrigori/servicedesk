"""The SLA sweeper has to be safe to run over and over."""

import pytest
from freezegun import freeze_time

from tickets import services, tasks
from tickets.models import AuditVerb, Priority, Status

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def run_celery_inline(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True


def test_sweeper_flags_a_late_ticket_once(requester, team, urgent_policy):
    with freeze_time("2026-03-03 13:00:00") as clock:
        ticket = services.create_ticket(
            title="Payment webhook is down",
            requester=requester,
            team=team,
            priority=Priority.URGENT,
        )
        clock.move_to("2026-03-04 13:00:00")  # a full day later
        first = tasks.sweep_sla_breaches()
        second = tasks.sweep_sla_breaches()

    assert first == {"first_response": 1, "resolution": 1}
    assert second == {"first_response": 0, "resolution": 0}

    ticket.refresh_from_db()
    assert ticket.first_response_breached
    assert ticket.resolution_breached
    assert ticket.events.filter(verb=AuditVerb.SLA_BREACHED).count() == 2


def test_a_resolution_breach_escalates_to_the_lead(requester, team, urgent_policy, lead):
    with freeze_time("2026-03-03 13:00:00") as clock:
        ticket = services.create_ticket(
            title="Staging database is full",
            requester=requester,
            team=team,
            priority=Priority.URGENT,
        )
        clock.move_to("2026-03-04 13:00:00")
        tasks.sweep_sla_breaches()

    ticket.refresh_from_db()
    assert ticket.escalation_level == 1
    assert ticket.assignee == lead


def test_answered_tickets_keep_a_clean_response_record(requester, team, urgent_policy, agent):
    with freeze_time("2026-03-03 13:00:00") as clock:
        ticket = services.create_ticket(
            title="VPN drops", requester=requester, team=team, priority=Priority.URGENT
        )
        services.add_comment(ticket, agent, "Looking now.")
        clock.move_to("2026-03-04 13:00:00")
        counts = tasks.sweep_sla_breaches()

    assert counts["first_response"] == 0
    ticket.refresh_from_db()
    assert not ticket.first_response_breached


def test_closed_tickets_are_left_alone(requester, team, urgent_policy, agent):
    with freeze_time("2026-03-03 13:00:00") as clock:
        ticket = services.create_ticket(
            title="Printer offline", requester=requester, team=team, priority=Priority.URGENT
        )
        services.transition(ticket, Status.RESOLVED, actor=agent)
        services.transition(ticket, Status.CLOSED, actor=agent)
        clock.move_to("2026-03-10 13:00:00")
        counts = tasks.sweep_sla_breaches()

    assert counts == {"first_response": 0, "resolution": 0}


def test_escalating_a_finished_ticket_does_nothing(requester, team, policy, agent):
    ticket = services.create_ticket(title="Done already", requester=requester, team=team)
    services.transition(ticket, Status.CANCELLED, actor=agent)

    assert tasks.escalate_ticket(ticket.pk, reason="late") is None

    ticket.refresh_from_db()
    assert ticket.escalation_level == 0


def test_escalating_a_missing_ticket_is_not_an_error():
    assert tasks.escalate_ticket(999999) is None
