"""
FleetBooks REST API.

  /api/auth/      login (JWT), token refresh, current user, users, business settings
  /api/driver/    the driver app: own trips, fuel, expenses, problem reports, vehicle, profile
  /api/office/    the office app: dashboard, every module's list + form, approvals, drivers
  /api/...        module endpoints (vehicles, bookings, income, expenses, fuel, loans/EMI, reports)
  /media/...      uploaded files, for signed-in users only (Bearer token or ?token=)
  /api/docs/      interactive API documentation (Swagger UI); /api/schema/ is the OpenAPI file
  /admin/         Django's built-in admin, for superusers (optional)
"""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path, re_path

from django.conf import settings
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from common.media import protected_media

from . import api_docs  # noqa: F401  (adds descriptions to the function endpoints)


def api_root(request):
    return JsonResponse({
        "name": "FleetBooks API",
        "auth": {"login": "/api/auth/login/", "refresh": "/api/auth/refresh/", "me": "/api/auth/me/"},
        "driver": "/api/driver/",
        "office": "/api/office/",
        "docs": "/api/docs/" if settings.API_DOCS else "See README.md for every endpoint.",
    })


def not_found(request, exception=None):
    return JsonResponse({"detail": "Not found. See / for the API sections."}, status=404)


def server_error(request):
    return JsonResponse({"detail": "The server had a problem. Please try again."}, status=500)


handler404 = not_found
handler500 = server_error

urlpatterns = [
    path("", api_root, name="api-root"),
    path("api/", api_root),
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/driver/", include("portal.api_urls")),      # drivers only, own records only
    path("api/office/", include("office.urls")),          # office users (by role); drivers get 403
    path("api/", include("fleet.urls")),
    path("api/", include("bookings.urls")),
    path("api/", include("finance.urls")),
    path("api/", include("reports.urls")),
    re_path(r"^media/(?P<path>.*)$", protected_media),
]

if settings.API_DOCS:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    ]

# Any other address ending in "/" gets a JSON 404 (also while DEBUG is on). Addresses without the
# final "/" are first redirected to the "/" form by Django, then land here.
urlpatterns += [re_path(r"^.*/$", not_found)]
