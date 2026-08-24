"""Endpoint behaviour, with an eye on who is allowed to see what."""

import pytest
from django.urls import reverse

from tickets import services
from tickets.models import Priority, Status

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/tickets/"


def detail_url(ticket, suffix=""):
    return f"{LIST_URL}{ticket.pk}/{suffix}"


def test_anonymous_callers_are_turned_away(api):
    assert api.get(LIST_URL).status_code == 401


def test_a_requester_can_open_a_ticket(as_requester, team, policy):
    response = as_requester.post(
        LIST_URL,
        {"title": "Cannot reach the VPN", "description": "since this morning", "team": "infra"},
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["reference"].startswith("TCK-")
    assert body["requester"]["username"] == "requester1"
    assert body["sla"]["resolution_due_at"] is not None


def test_requesters_only_see_their_own_tickets(as_requester, requester, make_user, team, policy):
    stranger = make_user("stranger")
    services.create_ticket(title="Not yours", requester=stranger, team=team)
    services.create_ticket(title="Yours", requester=requester, team=team)

    results = as_requester.get(LIST_URL).json()["results"]

    assert [item["title"] for item in results] == ["Yours"]


def test_agents_see_the_whole_team_queue(as_agent, requester, team, policy):
    services.create_ticket(title="Queue item", requester=requester, team=team)

    results = as_agent.get(LIST_URL).json()["results"]

    assert len(results) == 1


def test_a_ticket_is_listed_once_even_when_it_matches_twice(
    as_agent, requester, team, policy, agent
):
    """The agent is on the team and holds the ticket: still one row."""
    ticket = services.create_ticket(title="Mine twice over", requester=requester, team=team)
    services.assign(ticket, agent, actor=agent)

    body = as_agent.get(LIST_URL).json()

    assert body["count"] == 1
    assert len(body["results"]) == 1


def test_a_requester_cannot_move_a_ticket(as_requester, requester, team, policy):
    ticket = services.create_ticket(title="Mine", requester=requester, team=team)

    response = as_requester.post(
        detail_url(ticket, "transition/"), {"status": Status.RESOLVED}, format="json"
    )

    assert response.status_code == 403


def test_an_agent_moves_a_ticket_through_the_machine(as_agent, requester, team, policy):
    ticket = services.create_ticket(title="Work me", requester=requester, team=team)

    response = as_agent.post(
        detail_url(ticket, "transition/"), {"status": Status.IN_PROGRESS}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["status"] == Status.IN_PROGRESS


def test_an_illegal_move_answers_409_and_says_why(as_agent, requester, team, policy, agent):
    ticket = services.create_ticket(title="Cancelled", requester=requester, team=team)
    services.transition(ticket, Status.CANCELLED, actor=agent)

    response = as_agent.post(
        detail_url(ticket, "transition/"), {"status": Status.IN_PROGRESS}, format="json"
    )

    assert response.status_code == 409
    assert "cancelled" in response.json()["detail"]


def test_tickets_can_only_be_assigned_to_agents(as_agent, requester, team, policy):
    ticket = services.create_ticket(title="Assign me", requester=requester, team=team)

    response = as_agent.post(
        detail_url(ticket, "assign/"), {"assignee": requester.username}, format="json"
    )

    assert response.status_code == 400


def test_assigning_to_an_agent_works(as_agent, requester, team, policy, lead):
    ticket = services.create_ticket(title="Assign me", requester=requester, team=team)

    response = as_agent.post(
        detail_url(ticket, "assign/"), {"assignee": lead.username}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["assignee"]["username"] == lead.username


def test_internal_notes_are_hidden_from_the_requester(
    as_requester, as_agent, requester, team, policy, agent
):
    ticket = services.create_ticket(title="Sensitive", requester=requester, team=team)
    services.add_comment(ticket, agent, "Public reply")
    services.add_comment(
        ticket, agent, "Suspect the user clicked a phishing link", is_internal=True
    )

    seen_by_requester = as_requester.get(detail_url(ticket)).json()["comments"]
    seen_by_agent = as_agent.get(detail_url(ticket)).json()["comments"]

    assert [c["body"] for c in seen_by_requester] == ["Public reply"]
    assert len(seen_by_agent) == 2


def test_a_requester_cannot_write_an_internal_note(as_requester, requester, team, policy):
    ticket = services.create_ticket(title="Mine", requester=requester, team=team)

    response = as_requester.post(
        detail_url(ticket, "comments/"),
        {"body": "let me in", "is_internal": True},
        format="json",
    )

    assert response.status_code == 400


def test_a_requester_can_reply_on_their_own_ticket(as_requester, requester, team, policy):
    ticket = services.create_ticket(title="Mine", requester=requester, team=team)

    response = as_requester.post(
        detail_url(ticket, "comments/"), {"body": "Any update?"}, format="json"
    )

    assert response.status_code == 201
    ticket.refresh_from_db()
    assert ticket.first_responded_at is None  # the requester talking is not a response


def test_a_public_reply_from_an_agent_shows_up_in_the_sla_block(as_agent, requester, team, policy):
    ticket = services.create_ticket(title="Answer me", requester=requester, team=team)

    as_agent.post(detail_url(ticket, "comments/"), {"body": "On it."}, format="json")
    body = as_agent.get(detail_url(ticket)).json()

    assert body["sla"]["first_responded_at"] is not None


def test_raising_priority_through_the_api_recomputes_the_deadline(
    as_agent, requester, team, policy, urgent_policy
):
    ticket = services.create_ticket(title="Escalating", requester=requester, team=team)
    before = as_agent.get(detail_url(ticket)).json()["sla"]["resolution_due_at"]

    response = as_agent.post(
        detail_url(ticket, "priority/"), {"priority": Priority.URGENT}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["sla"]["resolution_due_at"] < before


def test_the_detail_view_carries_the_audit_trail(as_agent, requester, team, policy, agent):
    ticket = services.create_ticket(title="History", requester=requester, team=team)
    services.transition(ticket, Status.IN_PROGRESS, actor=agent)

    events = as_agent.get(detail_url(ticket)).json()["events"]

    assert [event["verb"] for event in events] == ["created", "status_changed"]


def test_filtering_by_status_and_breach(as_agent, requester, team, policy, agent):
    late = services.create_ticket(title="Late one", requester=requester, team=team)
    services.create_ticket(title="Fine one", requester=requester, team=team)
    late.resolution_breached_at = late.created_at
    late.save(update_fields=["resolution_breached_at"])

    results = as_agent.get(f"{LIST_URL}?breached=true").json()["results"]

    assert [item["title"] for item in results] == ["Late one"]


def test_search_matches_the_reference(as_agent, requester, team, policy):
    ticket = services.create_ticket(title="Findable", requester=requester, team=team)

    results = as_agent.get(f"{LIST_URL}?search={ticket.reference}").json()["results"]

    assert len(results) == 1


def test_the_sla_report_is_agents_only(as_requester, as_agent, requester, team, policy):
    services.create_ticket(title="Counted", requester=requester, team=team)

    assert as_requester.get("/api/v1/reports/sla/").status_code == 403

    body = as_agent.get("/api/v1/reports/sla/").json()
    assert body["totals"]["total"] == 1
    assert body["by_status"]["new"] == 1


def test_the_status_breakdown_counts_every_ticket(as_agent, requester, team, policy, agent):
    """Regression: the model's default ordering used to leak into the GROUP BY,
    which returned one row per ticket instead of one row per status."""
    for index in range(4):
        ticket = services.create_ticket(title=f"Open {index}", requester=requester, team=team)
        # An agent who is both a team member and the assignee matches the
        # visibility filter twice, which used to double count the ticket.
        services.assign(ticket, agent, actor=agent)
    worked = services.create_ticket(title="Working", requester=requester, team=team)
    services.transition(worked, Status.IN_PROGRESS, actor=agent)

    body = as_agent.get("/api/v1/reports/sla/").json()

    assert body["by_status"] == {"new": 4, "in_progress": 1}
    assert body["totals"]["total"] == 5
    assert sum(body["by_status"].values()) == body["totals"]["total"]


def test_the_openapi_schema_builds(as_agent):
    response = as_agent.get(reverse("schema"))

    assert response.status_code == 200


def test_the_jwt_flow_issues_a_working_token(api, agent):
    """Exercise the real token endpoint, not just forced authentication."""
    agent.set_password("demo12345")
    agent.save(update_fields=["password"])

    tokens = api.post(
        "/api/v1/auth/token/",
        {"username": agent.username, "password": "demo12345"},
        format="json",
    )
    assert tokens.status_code == 200

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.json()['access']}")
    assert api.get(LIST_URL).status_code == 200


def test_a_bad_password_does_not_issue_a_token(api, agent):
    response = api.post(
        "/api/v1/auth/token/",
        {"username": agent.username, "password": "wrong"},
        format="json",
    )

    assert response.status_code == 401
