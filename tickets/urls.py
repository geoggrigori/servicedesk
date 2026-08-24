from django.urls import include, path
from rest_framework.routers import DefaultRouter

from tickets.views import SlaReportView, TeamViewSet, TicketViewSet

router = DefaultRouter()
router.register("tickets", TicketViewSet, basename="ticket")
router.register("teams", TeamViewSet, basename="team")

urlpatterns = [
    path("", include(router.urls)),
    path("reports/sla/", SlaReportView.as_view(), name="sla-report"),
]
