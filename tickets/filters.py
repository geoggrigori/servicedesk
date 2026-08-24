import django_filters as filters
from django.db.models import Q

from tickets.models import Priority, Status, Ticket

#: A ticket counts as breached if either clock ran out on it.
BREACHED = Q(first_response_breached_at__isnull=False) | Q(resolution_breached_at__isnull=False)


class TicketFilter(filters.FilterSet):
    """Query helpers a desk actually uses: my queue, late, unassigned."""

    status = filters.MultipleChoiceFilter(choices=Status.choices)
    priority = filters.MultipleChoiceFilter(choices=Priority.choices)
    team = filters.CharFilter(field_name="team__slug")
    assignee = filters.CharFilter(field_name="assignee__username")
    requester = filters.CharFilter(field_name="requester__username")
    unassigned = filters.BooleanFilter(field_name="assignee", lookup_expr="isnull")
    breached = filters.BooleanFilter(method="filter_breached")
    escalated = filters.BooleanFilter(method="filter_escalated")

    class Meta:
        model = Ticket
        fields = ("status", "priority", "team", "assignee", "requester")

    def filter_breached(self, queryset, name, value):
        return queryset.filter(BREACHED) if value else queryset.exclude(BREACHED)

    def filter_escalated(self, queryset, name, value):
        if value:
            return queryset.filter(escalation_level__gt=0)
        return queryset.filter(escalation_level=0)
