"""Role based access rules for the ticket endpoints."""

from rest_framework.permissions import SAFE_METHODS, BasePermission

#: Actions a requester may take on a ticket they can already see. Replying is
#: one of them: shutting requesters out of their own thread would be absurd.
OPEN_ACTIONS = {"create", "comments"}


class IsAgentOrReadOnly(BasePermission):
    """Requesters open, read and reply; only agents work the ticket."""

    message = "Only agents can change tickets."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS or view.action in OPEN_ACTIONS:
            return True
        return bool(request.user and request.user.is_agent)


class IsAgent(BasePermission):
    message = "Only agents can do that."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_agent)
