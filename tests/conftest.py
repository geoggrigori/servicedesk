from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tickets.models import Priority, SlaPolicy, Team, TeamMembership
from tickets.sla import BusinessCalendar

User = get_user_model()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def at(year, month, day, hour=0, minute=0):
    """Build an aware datetime in the calendar's timezone."""
    return datetime(year, month, day, hour, minute, tzinfo=SAO_PAULO)


@pytest.fixture(autouse=True)
def test_environment(settings):
    """Strip away production-only behaviour the tests should not exercise.

    Django forces DEBUG off while testing, which switches on the HTTPS
    redirect, so every request would answer 301 instead of doing its job.
    """
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.SECURE_SSL_REDIRECT = False
    settings.SECURE_HSTS_SECONDS = 0


@pytest.fixture
def calendar():
    """Mon to Fri, 09:00 to 18:00, no holidays."""
    return BusinessCalendar(timezone="America/Sao_Paulo")


@pytest.fixture
def make_user(db):
    def _make(username, role="requester", **extra):
        return User.objects.create_user(username=username, password="test12345", role=role, **extra)

    return _make


@pytest.fixture
def requester(make_user):
    return make_user("requester1")


@pytest.fixture
def agent(make_user):
    return make_user("agent1", role="agent")


@pytest.fixture
def lead(make_user):
    return make_user("lead1", role="agent")


@pytest.fixture
def admin_user(make_user):
    return make_user("admin1", role="admin")


@pytest.fixture
def team(db, agent, lead):
    team = Team.objects.create(name="Infrastructure", slug="infra")
    TeamMembership.objects.create(team=team, user=agent)
    TeamMembership.objects.create(team=team, user=lead, is_lead=True)
    return team


@pytest.fixture
def policy(db, team):
    return SlaPolicy.objects.create(
        name="Infra normal",
        team=team,
        priority=Priority.NORMAL,
        first_response_minutes=60,
        resolution_minutes=480,
    )


@pytest.fixture
def urgent_policy(db, team):
    return SlaPolicy.objects.create(
        name="Infra urgent",
        team=team,
        priority=Priority.URGENT,
        first_response_minutes=15,
        resolution_minutes=120,
    )


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def as_agent(agent):
    client = APIClient()
    client.force_authenticate(agent)
    return client


@pytest.fixture
def as_requester(requester):
    client = APIClient()
    client.force_authenticate(requester)
    return client
