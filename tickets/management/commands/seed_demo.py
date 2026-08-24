"""Populate a believable desk so the API and admin have something to show."""

from __future__ import annotations

import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tickets import services
from tickets.models import Priority, SlaPolicy, Status, Team, TeamMembership

User = get_user_model()

TEAMS = [("Infrastructure", "infra"), ("Applications", "apps"), ("Security", "security")]

TARGETS = {
    Priority.URGENT: (15, 240),
    Priority.HIGH: (60, 480),
    Priority.NORMAL: (240, 1440),
    Priority.LOW: (480, 2880),
}

SUBJECTS = [
    "VPN drops every few minutes",
    "Cannot sign in to the billing portal",
    "Nightly backup job failed twice in a row",
    "Laptop needs a disk encryption key reset",
    "Payment webhook returning 500 since the deploy",
    "Request access to the analytics dashboard",
    "Printer on the third floor is offline",
    "Phishing email reported by the finance team",
    "Staging database is out of disk",
    "Two factor app lost after phone change",
    "API latency spiked after the last release",
    "New starter needs an account and a mailbox",
]


class Command(BaseCommand):
    help = "Create demo users, teams, SLA policies and tickets."

    def add_arguments(self, parser):
        parser.add_argument("--tickets", type=int, default=40)
        parser.add_argument("--seed", type=int, default=7)

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(options["seed"])
        rng = random.Random(options["seed"])

        admin = self._user("admin", "admin", "Ada", "Admin", staff=True)
        agents = [
            self._user(f"agent{i}", "agent", name, "Agent")
            for i, name in enumerate(["Bruno", "Carla", "Diego", "Elisa"], start=1)
        ]
        requesters = [
            self._user(f"user{i}", "requester", name, "Requester")
            for i, name in enumerate(["Fabio", "Gabi", "Hugo", "Iris", "Joana"], start=1)
        ]

        teams = []
        for index, (name, slug) in enumerate(TEAMS):
            team, _ = Team.objects.get_or_create(slug=slug, defaults={"name": name})
            teams.append(team)
            lead = agents[index % len(agents)]
            for agent in agents:
                TeamMembership.objects.get_or_create(
                    team=team, user=agent, defaults={"is_lead": agent == lead}
                )
            for priority, (response, resolution) in TARGETS.items():
                SlaPolicy.objects.get_or_create(
                    team=team,
                    priority=priority,
                    defaults={
                        "name": f"{name} {priority}",
                        "first_response_minutes": response,
                        "resolution_minutes": resolution,
                    },
                )

        now = timezone.now()
        created = 0
        for index in range(options["tickets"]):
            ticket = services.create_ticket(
                title=rng.choice(SUBJECTS),
                description="Reported through the service portal.",
                requester=rng.choice(requesters),
                team=rng.choice(teams),
                priority=rng.choice(list(TARGETS)),
                actor=admin,
            )
            # Backdate so the demo board has history instead of one flat day.
            age = timedelta(hours=rng.randint(1, 240))
            ticket.created_at = now - age
            services.apply_sla(ticket)
            ticket.save(
                update_fields=[
                    "created_at",
                    "sla_policy",
                    "first_response_due_at",
                    "resolution_due_at",
                ]
            )
            self._advance(ticket, agents, rng, index)
            created += 1

        breaches = services.sweep_breaches()

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {created} tickets, {len(teams)} teams, "
                f"{len(agents)} agents, {breaches['resolution']} past due. "
                f"Log in as admin / demo12345."
            )
        )

    def _advance(self, ticket, agents, rng, index):
        """Push some tickets down the workflow so every status is represented."""
        roll = index % 5
        if roll == 0:
            return
        agent = rng.choice(agents)
        services.assign(ticket, agent, actor=agent)
        services.add_comment(ticket, agent, "Looking into this now.")
        services.transition(ticket, Status.IN_PROGRESS, actor=agent)
        if roll == 2:
            services.transition(ticket, Status.PENDING, actor=agent)
        elif roll in (3, 4):
            services.transition(ticket, Status.RESOLVED, actor=agent)
            if roll == 4:
                services.transition(ticket, Status.CLOSED, actor=agent)

    def _user(self, username, role, first, last, staff=False):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "role": role,
                "first_name": first,
                "last_name": last,
                "email": f"{username}@example.com",
                "is_staff": staff or role == "admin",
                "is_superuser": role == "admin",
            },
        )
        if created:
            user.set_password("demo12345")
            user.save(update_fields=["password"])
        return user
