from django.contrib import admin
from django.urls import include, path
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView


@extend_schema(tags=["auth"])
class TokenObtainView(TokenObtainPairView):
    """Exchange username and password for an access and a refresh token."""


@extend_schema(tags=["auth"])
class TokenRefreshApiView(TokenRefreshView):
    """Trade a refresh token for a fresh access token."""


admin.site.site_header = "Servicedesk"
admin.site.site_title = "Servicedesk admin"
admin.site.index_title = "Service desk operations"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("tickets.urls")),
    path("api/v1/auth/token/", TokenObtainView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshApiView.as_view(), name="token_refresh"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
