"""
Driver API  —  /api/driver/...

For a driver mobile app or any other client. Log in with the normal token
endpoint (POST /api/auth/login/ {"username", "password"}) and send
"Authorization: Bearer <access token>". A logged-in browser session works too.

* Only users with the Driver role and an active driver profile get in (others: 403).
* Every lookup is filtered by the logged-in driver. Asking for another driver's
  trip, expense or report returns 404, so a driver can't even learn it exists.
* Validation uses portal.forms, and the work is done by portal.actions,
  so every rule lives in one place.
* Uploads (receipt, photo, trip_document, extra_receipt): send multipart/form-data.
"""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from bookings.models import Booking
from finance.models import Expense, FuelEntry, Maintenance

from . import actions
from .actions import ActionError
from .forms import (CompleteTripForm, ExpenseForm, FuelForm, PasswordForm, ProblemForm, ProfileForm, StartTripForm,
                    UpdateTripForm)


class IsActiveDriver(BasePermission):
    message = "The Driver API is only for drivers with an active account."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and getattr(user, "is_driver", False)):
            return False
        driver = getattr(user, "driver_profile", None)
        if driver is None or driver.status == "inactive":
            return False
        request.driver = driver
        return True


def driver_api(methods):
    """Decorator stack shared by every Driver API endpoint."""
    def wrap(fn):
        fn = permission_classes([IsActiveDriver])(fn)
        fn = authentication_classes([JWTAuthentication, SessionAuthentication])(fn)
        return api_view(methods)(fn)
    return wrap


# ------------------------------------------------------------------ output shapes
def _file(request, f):
    return request.build_absolute_uri(f.url) if f else None


def vehicle_out(v):
    if not v:
        return None
    return {"id": v.pk, "registration_number": v.registration_number, "name": v.short_name, "label": v.label,
            "type": v.vehicle_type.name, "brand": v.brand, "model": v.model, "fuel_type": v.fuel_type,
            "fuel_type_label": v.get_fuel_type_display(), "seating_capacity": v.seating_capacity}


def trip_out(request, b, detail=False):
    """What a driver may know about a trip — no booking amount, advance or balance."""
    out = {"id": b.pk, "trip_number": b.booking_number, "status": b.driver_stage, "status_label": b.driver_stage_label,
           "customer": b.customer.name, "vehicle": {"id": b.vehicle_id, "label": b.vehicle.label},
           "pickup_location": b.pickup_location, "destination": b.destination,
           "trip_date": b.trip_date, "return_date": b.return_date, "number_of_days": b.number_of_days,
           "can_accept": b.driver_stage == "assigned", "can_start": b.driver_stage in ("assigned", "accepted"),
           "can_complete": b.driver_stage == "ongoing"}
    if detail:
        out.update({
            "customer_phone": b.customer.phone, "office_notes": b.notes,
            "accepted_at": b.accepted_at, "started_at": b.started_at, "start_odometer": b.start_odometer,
            "start_location": b.start_location, "completed_at": b.completed_at, "end_odometer": b.end_odometer,
            "end_location": b.end_location, "total_km": b.total_km, "duration": b.duration_text or None,
            "trip_notes": b.trip_notes, "trip_document": _file(request, b.trip_document),
            "fuel": [fuel_out(request, f) for f in FuelEntry.objects.filter(booking=b, driver=request.driver)],
            "expenses": [expense_out(request, e) for e in Expense.all_objects.filter(booking=b, driver=request.driver)],
        })
    return out


def fuel_out(request, f):
    return {"id": f.pk, "date": f.date, "vehicle": f.vehicle.label, "vehicle_id": f.vehicle_id, "trip_id": f.booking_id,
            "fuel_type": f.fuel_type, "litres": f.litres, "rate": f.rate, "total_amount": f.total_amount,
            "odometer": f.odometer, "fuel_station": f.fuel_station, "payment_method": f.payment_method,
            "receipt": _file(request, f.receipt), "notes": f.notes}


def expense_out(request, e):
    return {"id": e.pk, "date": e.date, "vehicle": e.vehicle.label if e.vehicle else None, "vehicle_id": e.vehicle_id,
            "trip_id": e.booking_id, "category": e.category, "category_label": e.get_category_display(),
            "amount": e.amount, "description": e.description, "payment_method": e.payment_method,
            "status": e.status, "status_label": e.get_status_display(), "rejection_reason": e.rejection_reason or None,
            "receipt": _file(request, e.receipt), "notes": e.notes, "can_withdraw": e.status == "pending"}


def problem_out(request, m):
    return {"id": m.pk, "date": m.date, "vehicle": m.vehicle.label, "vehicle_id": m.vehicle_id,
            "problem_category": m.problem_category, "problem_label": m.get_problem_category_display(),
            "priority": m.priority, "status": m.status, "status_label": m.get_status_display(),
            "description": m.description, "odometer": m.odometer, "photo": _file(request, m.photo),
            "workshop": m.workshop or None, "notes": m.notes}


def _invalid(form):
    return Response({"errors": {k: [str(x) for x in v] for k, v in form.errors.items()}},
                    status=status.HTTP_400_BAD_REQUEST)


def _rule(e):
    return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)


def _now_default(data, key):
    """Start/end time may be left out: it then means 'now'. ISO date-times are accepted."""
    data = data.copy()
    value = data.get(key)
    if not value:
        data[key] = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    elif isinstance(value, str) and len(value) > 16:
        try:
            parsed = timezone.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timezone.is_aware(parsed):
                parsed = timezone.localtime(parsed)
            data[key] = parsed.strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            pass
    return data


def _my_trip(request, pk):
    return get_object_or_404(Booking.objects.select_related("customer", "vehicle"), pk=pk, driver=request.driver)


# ------------------------------------------------------------------ endpoints
@driver_api(["GET"])
def dashboard(request):
    d = request.driver
    data = actions.dashboard_data(d)
    return Response({
        "driver": {"id": d.pk, "name": d.name}, "vehicle": vehicle_out(d.assigned_vehicle),
        "current_trip": trip_out(request, data["trip"]) if data["trip"] else None,
        "new_trips_to_accept": data["new_trips"], "stats": {**data["stats"], "km_this_month": data["km_month"]},
        "trips_to_accept": [trip_out(request, b) for b in data["to_accept"]],
        "coming_up": [trip_out(request, b) for b in data["coming_up"]],
        "unread_notifications": request.user.notifications.filter(is_read=False).count(),
    })


@driver_api(["GET"])
def trips(request):
    show = request.query_params.get("show", "active")
    qs = Booking.objects.filter(driver=request.driver).select_related("customer", "vehicle")
    if show == "active":
        qs = qs.filter(trip_status__in=["upcoming", "ongoing"]).order_by("trip_date")
    elif show == "completed":
        qs = qs.filter(trip_status="completed").order_by("-trip_date")
    else:
        qs = qs.order_by("-trip_date")
    return Response({"results": [trip_out(request, b) for b in qs[:200]]})


@driver_api(["GET"])
def trip_detail(request, pk):
    return Response(trip_out(request, _my_trip(request, pk), detail=True))


@driver_api(["POST"])
def trip_accept(request, pk):
    trip = _my_trip(request, pk)
    try:
        actions.accept_trip(request.driver, request.user, trip)
    except ActionError as e:
        return _rule(e)
    return Response(trip_out(request, trip, detail=True))


@driver_api(["POST"])
def trip_start(request, pk):
    trip = _my_trip(request, pk)
    form = StartTripForm(_now_default(request.data, "started_at"))
    if not form.is_valid():
        return _invalid(form)
    try:
        actions.start_trip(request.driver, request.user, trip, form.cleaned_data)
    except ActionError as e:
        return _rule(e)
    return Response(trip_out(request, trip, detail=True))


@driver_api(["POST"])
def trip_update(request, pk):
    trip = _my_trip(request, pk)
    form = UpdateTripForm(request.data, request.FILES)
    if not form.is_valid():
        return _invalid(form)
    try:
        actions.update_trip(request.driver, request.user, trip, form.cleaned_data)
    except ActionError as e:
        return _rule(e)
    return Response(trip_out(request, trip, detail=True))


@driver_api(["POST"])
def trip_complete(request, pk):
    trip = _my_trip(request, pk)
    if trip.trip_status != "ongoing":
        return _rule("Start the trip before completing it.")
    form = CompleteTripForm(_now_default(request.data, "completed_at"), request.FILES, booking=trip)
    if not form.is_valid():
        return _invalid(form)
    actions.complete_trip(request.driver, request.user, trip, form.cleaned_data)
    return Response(trip_out(request, trip, detail=True))


@driver_api(["GET", "POST"])
def fuel(request):
    if request.method == "POST":
        form = FuelForm(request.data, request.FILES, driver=request.driver)
        if not form.is_valid():
            return _invalid(form)
        entry = actions.add_fuel(request.driver, request.user, form.cleaned_data)
        return Response(fuel_out(request, entry), status=status.HTTP_201_CREATED)
    rows = FuelEntry.objects.filter(driver=request.driver).select_related("vehicle")[:200]
    return Response({"results": [fuel_out(request, f) for f in rows]})


@driver_api(["GET", "POST"])
def expenses(request):
    if request.method == "POST":
        form = ExpenseForm(request.data, request.FILES, driver=request.driver)
        if not form.is_valid():
            return _invalid(form)
        exp = actions.add_expense(request.driver, request.user, form.cleaned_data)
        return Response(expense_out(request, exp), status=status.HTTP_201_CREATED)
    qs = Expense.all_objects.filter(driver=request.driver).select_related("vehicle")
    if request.query_params.get("status"):
        qs = qs.filter(status=request.query_params["status"])
    return Response({"results": [expense_out(request, e) for e in qs[:200]]})


@driver_api(["GET"])
def expense_detail(request, pk):
    return Response(expense_out(request, get_object_or_404(Expense.all_objects, pk=pk, driver=request.driver)))


@driver_api(["POST"])
def expense_withdraw(request, pk):
    exp = get_object_or_404(Expense.all_objects, pk=pk, driver=request.driver)
    try:
        actions.withdraw_expense(request.driver, request.user, exp)
    except ActionError as e:
        return _rule(e)
    return Response(status=status.HTTP_204_NO_CONTENT)


@driver_api(["GET", "POST"])
def maintenance(request):
    if request.method == "POST":
        form = ProblemForm(request.data, request.FILES, driver=request.driver)
        if not form.is_valid():
            return _invalid(form)
        m = actions.report_problem(request.driver, request.user, form.cleaned_data)
        return Response(problem_out(request, m), status=status.HTTP_201_CREATED)
    rows = Maintenance.objects.filter(driver=request.driver).select_related("vehicle")[:200]
    return Response({"results": [problem_out(request, m) for m in rows]})


@driver_api(["GET"])
def maintenance_detail(request, pk):
    return Response(problem_out(request, get_object_or_404(Maintenance, pk=pk, driver=request.driver)))


def vehicle_documents(v):
    """Latest document of each type for a vehicle, soonest expiry first."""
    from fleet.models import VehicleDocument
    latest = {}
    for doc in VehicleDocument.objects.filter(vehicle=v).order_by("doc_type", "-expiry_date"):
        latest.setdefault(doc.doc_type, doc)
    return sorted(latest.values(), key=lambda x: x.expiry_date)


@driver_api(["GET"])
def vehicle(request):
    v = request.driver.assigned_vehicle
    return Response({
        "vehicle": vehicle_out(v),
        "documents": [{"type": d.doc_type, "label": d.get_doc_type_display(), "expiry_date": d.expiry_date,
                       "days_left": d.days_left, "status": d.status} for d in (vehicle_documents(v) if v else [])],
        "history": [{"vehicle": a.vehicle.label, "from": a.start_date, "to": a.end_date}
                    for a in request.driver.assignments.select_related("vehicle")[:20]],
    })


def profile_out(request, d):
    return {"id": d.pk, "name": d.name, "photo": _file(request, d.photo), "phone": d.phone, "email": d.email,
            "address": d.address, "blood_group": d.blood_group,
            "emergency_contact": {"name": d.emergency_contact_name, "phone": d.emergency_contact_phone},
            "username": request.user.username, "licence_number": d.licence_number, "licence_expiry": d.licence_expiry,
            "licence_status": d.licence_status, "joining_date": d.joining_date, "status": d.status,
            "assigned_vehicle": vehicle_out(d.assigned_vehicle),
            "editable_fields": list(ProfileForm.Meta.fields)}


@driver_api(["GET", "PATCH"])
def profile(request):
    """GET your profile. PATCH any of `editable_fields` (multipart for a new photo; photo-clear=true removes it)."""
    d = request.driver
    if request.method == "PATCH":
        data = {f: getattr(d, f) for f in ProfileForm.Meta.fields if f != "photo"}
        data["licence_expiry"] = d.licence_expiry.isoformat() if d.licence_expiry else ""
        data.update({k: v for k, v in request.data.items() if k in ProfileForm.Meta.fields or k == "photo-clear"})
        form = ProfileForm(data, request.FILES, instance=d)
        if not form.is_valid():
            return _invalid(form)
        d, _ = actions.update_profile(d, request.user, form)
    return Response(profile_out(request, d))


@driver_api(["POST"])
def change_password(request):
    form = PasswordForm(request.data, user=request.user)
    if not form.is_valid():
        return _invalid(form)
    request.user.set_password(form.cleaned_data["new_password"])
    request.user.save()
    return Response({"detail": "Password changed. Log in again with the new password."})


@driver_api(["GET"])
def notifications(request):
    rows = list(request.user.notifications.all()[:60])
    request.user.notifications.filter(pk__in=[n.pk for n in rows if not n.is_read]).update(is_read=True)
    return Response({"results": [{"id": n.pk, "text": n.text, "url": n.url, "is_read": n.is_read,
                                  "created_at": n.created_at} for n in rows]})


@driver_api(["GET"])
def form_options(request):
    """Choices for the driver's forms, taken from the server-side forms (portal.forms)
    (so vehicles and trips are only the driver's own)."""
    def ch(field):
        return [[str(v), str(label)] for v, label in field.choices if v not in ("", None)]

    fuel = FuelForm(driver=request.driver)
    exp = ExpenseForm(driver=request.driver)
    prob = ProblemForm(driver=request.driver)
    prof = ProfileForm(instance=request.driver)
    trips = [[str(b.pk), f"{b.booking_number} · {b.destination or b.customer.name} · {b.trip_date:%d %b}"]
             for b in fuel.my_trips.order_by("-trip_date")[:30]]
    return Response({
        "vehicles": [[str(v.pk), v.label] for v in fuel.my_vehicles],
        "default_vehicle": str(request.driver.assigned_vehicle_id or ""),
        "default_fuel_type": request.driver.assigned_vehicle.fuel_type if request.driver.assigned_vehicle_id else "",
        "trips": trips,
        "fuel_types": ch(fuel.fields["fuel_type"]),
        "payment_methods": ch(fuel.fields["payment_method"]),
        "expense_categories": ch(exp.fields["category"]),
        "problem_categories": ch(prob.fields["problem_category"]),
        "priorities": ch(prob.fields["priority"]),
        "blood_groups": ch(prof.fields["blood_group"]),
        "max_upload_mb": 10,
    })
