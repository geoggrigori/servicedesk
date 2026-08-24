"""Background jobs that watch the SLA clock.

These are deliberately thin: the rules live in `services`, so the same sweep
can run from a worker, a management command or a test without a broker.
"""

from __future__ import annotations

import logging

from celery import shared_task

from tickets import services
from tickets.models import Ticket

logger = logging.getLogger(__name__)


@shared_task
def sweep_sla_breaches() -> dict[str, int]:
    """Flag tickets that ran past a deadline since the last sweep."""
    counts = services.sweep_breaches()
    logger.info("SLA sweep flagged %s", counts)
    return counts


@shared_task
def escalate_ticket(ticket_id: int, reason: str = "manual") -> str | None:
    ticket = Ticket.objects.filter(pk=ticket_id).select_related("team").first()
    if ticket is None or not ticket.is_open:
        return None
    services.escalate(ticket, reason=reason)
    return ticket.reference
