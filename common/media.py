"""
Uploaded receipts, photos and document copies (/media/...).

Only signed-in users can open them: send the app's token as an
"Authorization: Bearer <access>" header, or as ?token=<access> when opening
a file in a viewer. Office users see files by role; drivers only their own.
"""
from django.conf import settings
from django.http import Http404, JsonResponse
from django.views.static import serve as static_serve
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


def driver_can_open(driver, path):
    """A driver may only open files attached to their own trips, fuel, expenses or problem reports."""
    from bookings.models import Booking
    from finance.models import Expense, FuelEntry, Maintenance
    if driver is None:
        return False
    return (driver.photo.name == path
            or Expense.all_objects.filter(driver=driver, receipt=path).exists()
            or FuelEntry.objects.filter(driver=driver, receipt=path).exists()
            or Maintenance.objects.filter(driver=driver, photo=path).exists()
            or Booking.objects.filter(driver=driver, trip_document=path).exists())


def _token_user(request):
    if request.user.is_authenticated:          # Django admin session
        return request.user
    auth = JWTAuthentication()
    raw = request.GET.get("token")
    header = request.META.get("HTTP_AUTHORIZATION", "")
    try:
        if header.startswith("Bearer "):
            raw = header.split(" ", 1)[1]
        if raw:
            user = auth.get_user(auth.get_validated_token(raw))
            return user if user.is_active else None
    except (InvalidToken, TokenError):
        return None
    return None


def protected_media(request, path):
    user = _token_user(request)
    if user is None:
        return JsonResponse({"detail": "Authentication credentials were not provided."}, status=401)
    request.user = user
    if user.is_driver and not driver_can_open(getattr(user, "driver_profile", None), path):
        raise Http404
    return static_serve(request, path, document_root=settings.MEDIA_ROOT)
