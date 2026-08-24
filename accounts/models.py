"""User model with the three service desk roles."""

from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    """Who someone is inside the service desk."""

    ADMIN = "admin", "Admin"
    AGENT = "agent", "Agent"
    REQUESTER = "requester", "Requester"


class User(AbstractUser):
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.REQUESTER)
    job_title = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ("username",)

    def __str__(self) -> str:
        return self.get_full_name() or self.username

    @property
    def is_agent(self) -> bool:
        """Agents and admins can both work tickets."""
        return self.role in {Role.AGENT, Role.ADMIN}

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN or self.is_superuser
