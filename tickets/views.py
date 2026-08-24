"""API endpoints for tickets, teams and SLA reporting."""

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tickets import services
from tickets.filters import BREACHED, TicketFilter
from tickets.models import Status, Team, Ticket
from tickets.permissions import IsAgent, IsAgentOrReadOnly
from tickets.serializers import (
    CommentSerializer,
    TeamSerializer,
    TicketAssignSerializer,
    TicketCreateSerializer,
    TicketDetailSerializer,
    TicketPrioritySerializer,
    TicketSerializer,
    TicketTransitionSerializer,
)

#: Statuses that take a ticket off the board.
FINISHED = [Status.RESOLVED, Status.CLOSED, Status.CANCELLED]


@extend_schema(tags=["teams"])
class TeamViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Team.objects.all()
    serializer_class = TeamSerializer
    lookup_field = "slug"


@extend_schema(tags=["tickets"])
class TicketViewSet(viewsets.ModelViewSet):
    """Tickets, scoped to what the caller is allowed to see."""

    permission_classes = [IsAuthenticated, IsAgentOrReadOnly]
    filterset_class = TicketFilter
    search_fields = ("reference", "title", "description")
    ordering_fields = ("created_at", "updated_at", "priority", "resolution_due_at")
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        # The schema generator introspects the view with no real user attached.
        if getattr(self, "swagger_fake_view", False):
            return Ticket.objects.none()
        return (
            Ticket.objects.visible_to(self.request.user)
            .select_related("requester", "assignee", "team", "sla_policy")
            .order_by("-created_at")
        )

    def get_serializer_class(self):
        if self.action == "create":
            return TicketCreateSerializer
        if self.action == "retrieve":
            return TicketDetailSerializer
        return TicketSerializer

    def get_object(self):
        """Prefetch the history only where the detail serializer needs it."""
        queryset = self.get_queryset()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related("events__actor", "comments__author")
        obj = queryset.filter(pk=self.kwargs["pk"]).first()
        if obj is None:
            from django.http import Http404

            raise Http404
        self.check_object_permissions(self.request, obj)
        return obj

    def create(self, request, *args, **kwargs):
        payload = TicketCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ticket = services.create_ticket(requester=request.user, **payload.validated_data)
        output = TicketSerializer(ticket, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_201_CREATED)

    def _respond(self, ticket):
        ticket.refresh_from_db()
        return Response(TicketSerializer(ticket, context=self.get_serializer_context()).data)

    @extend_schema(
        request=TicketTransitionSerializer,
        responses={200: TicketSerializer, 409: OpenApiResponse(description="Illegal transition")},
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsAgent])
    def transition(self, request, pk=None):
        """Move the ticket to another status, if the machine allows it."""
        payload = TicketTransitionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ticket = self.get_object()
        try:
            services.transition(
                ticket,
                payload.validated_data["status"],
                actor=request.user,
                note=payload.validated_data.get("note", ""),
            )
        except services.InvalidTransition as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return self._respond(ticket)

    @extend_schema(request=TicketAssignSerializer, responses={200: TicketSerializer})
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsAgent])
    def assign(self, request, pk=None):
        payload = TicketAssignSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ticket = self.get_object()
        services.assign(ticket, payload.validated_data["assignee"], actor=request.user)
        return self._respond(ticket)

    @extend_schema(request=TicketPrioritySerializer, responses={200: TicketSerializer})
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsAgent])
    def priority(self, request, pk=None):
        """Change priority. Deadlines are recomputed under the new policy."""
        payload = TicketPrioritySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ticket = self.get_object()
        services.change_priority(ticket, payload.validated_data["priority"], actor=request.user)
        return self._respond(ticket)

    @extend_schema(request=CommentSerializer, responses={201: CommentSerializer})
    @action(detail=True, methods=["get", "post"])
    def comments(self, request, pk=None):
        ticket = self.get_object()
        if request.method == "GET":
            queryset = ticket.comments.select_related("author")
            if not request.user.is_agent:
                queryset = queryset.filter(is_internal=False)
            return Response(
                CommentSerializer(queryset, many=True, context={"request": request}).data
            )

        payload = CommentSerializer(data=request.data, context={"request": request})
        payload.is_valid(raise_exception=True)
        comment = services.add_comment(
            ticket,
            request.user,
            payload.validated_data["body"],
            is_internal=payload.validated_data.get("is_internal", False),
        )
        return Response(
            CommentSerializer(comment, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["reporting"], responses={200: OpenApiResponse(description="SLA counters")})
class SlaReportView(APIView):
    """One aggregate query behind the queue health numbers."""

    permission_classes = [IsAuthenticated, IsAgent]

    def get(self, request):
        now = timezone.now()
        queryset = Ticket.objects.visible_to(request.user)
        still_open = ~Q(status__in=FINISHED)
        totals = queryset.aggregate(
            total=Count("id"),
            open=Count("id", filter=still_open),
            breached=Count("id", filter=BREACHED),
            escalated=Count("id", filter=Q(escalation_level__gt=0)),
            due_soon=Count(
                "id",
                filter=still_open
                & Q(
                    resolution_due_at__gte=now,
                    resolution_due_at__lte=now + timedelta(hours=4),
                ),
            ),
        )
        # order_by() is load bearing: the model's default ordering would
        # otherwise join the GROUP BY and return one row per ticket.
        by_status = dict(
            queryset.order_by()
            .values_list("status")
            .annotate(n=Count("id"))
            .values_list("status", "n")
        )
        return Response({"totals": totals, "by_status": by_status, "generated_at": now})
