"""
Office API  —  /api/office/...

Dashboard, generic list/add/edit/delete for every module, expense approvals,
driver management, assignments, activity log and notifications for office users
(Admin, Superadmin, Accountant, Manager, Staff). Drivers are refused here and use
/api/driver/ instead. Access follows accounts.permissions.access_for.
"""


from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from accounts.permissions import access_for
from bookings.models import Booking
from common.dates import month_bounds, today
from common.params import check_query, require_id
from finance.models import Expense, FuelEntry, Maintenance
from fleet.models import Driver, Vehicle
from portal.models import DriverActivity
from reports import services

from . import dashboard_data, driver_ops
from .dashboard_data import driver_overview
from .resource_ops import can, delete_resource, filtered_rows, submit_resource, viewset_for
from .resources import RESOURCES


def _need(request, module, write=False):
    level = access_for(request.user).get(module)
    if not level or (write and level != "rw"):
        return Response({"detail": "Your role doesn't allow this."}, status=status.HTTP_403_FORBIDDEN)
    return None


def _file(request, f):
    return request.build_absolute_uri(f.url) if f else None


def _vehicle(v):
    return {"id": v.pk, "label": v.label, "registration_number": v.registration_number, "name": v.short_name,
            "type": v.vehicle_type.name, "status": v.status} if v else None


def _trip(b):
    return {"id": b.pk, "booking_number": b.booking_number, "trip_date": b.trip_date, "return_date": b.return_date,
            "pickup_location": b.pickup_location, "destination": b.destination, "customer": b.customer.name,
            "vehicle": _vehicle(b.vehicle), "driver": {"id": b.driver_id, "name": b.driver.name} if b.driver_id else None,
            "trip_status": b.trip_status, "stage": b.driver_stage, "stage_label": b.driver_stage_label,
            "total_km": b.total_km, "duration": b.duration_text or None,
            "started_at": b.started_at, "completed_at": b.completed_at}


def _expense(request, e):
    return {"id": e.pk, "date": e.date, "category": e.category, "category_label": e.get_category_display(),
            "amount": e.amount, "description": e.description, "notes": e.notes, "status": e.status,
            "status_label": e.get_status_display(), "rejection_reason": e.rejection_reason,
            "payment_method_label": e.get_payment_method_display(), "receipt": _file(request, e.receipt),
            "vehicle": _vehicle(e.vehicle), "booking_number": e.booking.booking_number if e.booking_id else None,
            "driver": {"id": e.driver_id, "name": e.driver.name} if e.driver_id else None,
            "reviewed_by": e.reviewed_by.display_name if e.reviewed_by_id else None, "reviewed_at": e.reviewed_at}


def _activity(a):
    return {"id": a.pk, "created_at": a.created_at, "driver": {"id": a.driver_id, "name": a.driver.name},
            "action": a.action, "text": a.text, "vehicle": a.vehicle.label if a.vehicle_id else None,
            "booking_number": a.booking.booking_number if a.booking_id else None, "amount": a.amount, "status": a.status}


# ------------------------------------------------------------------ dashboard
@api_view(["GET"])
def dashboard(request):
    data = services.dashboard(request.user)
    board, counts = dashboard_data.road_board()
    rows = [{
        "vehicle": _vehicle(r["v"]), "state": r["state"], "when": r.get("when", ""), "progress": r.get("progress"),
        "driver": {"id": r["driver"].pk, "name": r["driver"].name} if r["driver"] else None,
        "trip": _trip(r["trip"]) if r["trip"] else None,
    } for r in board]
    can_drivers = bool(access_for(request.user).get("drivers"))
    return Response({**data, "board": rows, "board_counts": counts,
                     "attention": dashboard_data.attention(request.user, data),
                     "ledger": dashboard_data.ledger(data["finance"]) if data.get("finance") else None,
                     "drivers": driver_overview() if can_drivers else None})


# ------------------------------------------------------------------ expense approvals
@api_view(["GET"])
def expense_approvals(request):
    denied = _need(request, "expenses", write=True)
    if denied:
        return denied
    pending = Expense.all_objects.filter(status="pending").select_related("vehicle", "booking", "driver").order_by("date", "id")
    recent = Expense.all_objects.filter(driver__isnull=False).exclude(status="pending") \
        .select_related("vehicle", "driver", "reviewed_by", "booking").order_by("-reviewed_at", "-id")[:30]
    return Response({"pending": [_expense(request, e) for e in pending], "recent": [_expense(request, e) for e in recent]})


@api_view(["POST"])
def expense_decide(request, pk):
    denied = _need(request, "expenses", write=True)
    if denied:
        return denied
    exp = get_object_or_404(Expense.all_objects, pk=pk)
    try:
        driver_ops.decide_expense(exp, request.user, request.data.get("action"), request.data.get("reason", ""))
    except driver_ops.OpError as e:
        return Response({"detail": str(e)}, status=400)
    return Response(_expense(request, exp))


# ------------------------------------------------------------------ driver profile, account, assignments
@api_view(["GET"])
def driver_overview_api(request, pk):
    denied = _need(request, "drivers")
    if denied:
        return denied
    d = get_object_or_404(Driver.objects.select_related("assigned_vehicle", "user"), pk=pk)
    t = today()
    raw = request.query_params.get("month", "")
    if raw:
        try:
            year, month = (int(x) for x in raw.split("-")[:2])
            start, end = month_bounds(year, month)
        except ValueError:
            return Response({"month": ["Use a month like 2026-10."]}, status=400)
    else:
        year, month = t.year, t.month
        start, end = month_bounds(year, month)
    trips = Booking.objects.filter(driver=d).select_related("vehicle", "customer", "driver").order_by("-trip_date")
    month_trips = trips.filter(trip_date__range=(start, end))
    fuel = FuelEntry.objects.filter(driver=d).select_related("vehicle", "booking")
    expenses = Expense.all_objects.filter(driver=d).select_related("vehicle", "booking", "driver", "reviewed_by")
    reports_ = Maintenance.objects.filter(driver=d).select_related("vehicle")
    summary = services.driver_summary(d, request.user, {"year": year, "month": month})
    return Response({
        "driver": {"id": d.pk, "name": d.name, "phone": d.phone, "email": d.email, "status": d.status,
                   "status_label": d.get_status_display(), "photo": _file(request, d.photo),
                   "licence_number": d.licence_number, "licence_expiry": d.licence_expiry, "licence_status": d.licence_status,
                   "address": d.address, "blood_group": d.blood_group, "emergency_contact_name": d.emergency_contact_name,
                   "emergency_contact_phone": d.emergency_contact_phone, "joining_date": d.joining_date,
                   "vehicle": _vehicle(d.assigned_vehicle)},
        "account": {"username": d.user.username, "is_active": d.user.is_active, "last_login": d.user.last_login}
        if d.user_id else None,
        "month": f"{year}-{month:02d}",
        "monthly": {
            "trips": month_trips.exclude(trip_status="cancelled").count(),
            "completed": month_trips.filter(trip_status="completed").count(),
            "km": sum((b.total_km or 0) for b in month_trips.filter(trip_status="completed")),
            "litres": fuel.filter(date__range=(start, end)).aggregate(x=Sum("litres"))["x"] or 0,
            "fuel_cost": fuel.filter(date__range=(start, end)).aggregate(x=Sum("total_amount"))["x"] or 0,
            "approved": expenses.filter(date__range=(start, end), status="approved").aggregate(x=Sum("amount"))["x"] or 0,
            "pending": expenses.filter(date__range=(start, end), status="pending").aggregate(x=Sum("amount"))["x"] or 0,
            "reports": reports_.filter(date__range=(start, end)).count(),
        },
        "salary": summary,
        "trips": [_trip(b) for b in trips[:100]],
        "fuel": [{"id": f.pk, "date": f.date, "vehicle": f.vehicle.label, "litres": f.litres, "rate": f.rate,
                  "total_amount": f.total_amount, "station": f.fuel_station, "receipt": _file(request, f.receipt),
                  "booking_number": f.booking.booking_number if f.booking_id else None} for f in fuel[:100]],
        "expenses": [_expense(request, e) for e in expenses[:100]],
        "maintenance": [{"id": m.pk, "date": m.date, "vehicle": m.vehicle.label, "problem": m.get_problem_category_display()
                         or m.get_maintenance_type_display(), "priority": m.priority, "priority_label": m.get_priority_display(),
                         "status": m.status, "status_label": m.get_status_display(), "description": m.description,
                         "total_cost": m.total_cost if m.status == "completed" else None, "photo": _file(request, m.photo)}
                        for m in reports_[:100]],
        "assignments": [{"id": a.pk, "vehicle": a.vehicle.label, "start_date": a.start_date, "end_date": a.end_date,
                         "assigned_by": a.assigned_by.display_name if a.assigned_by_id else None, "notes": a.notes}
                        for a in d.assignments.select_related("vehicle", "assigned_by")[:50]],
        "activity": [_activity(a) for a in DriverActivity.objects.filter(driver=d).select_related("driver", "vehicle", "booking")[:100]],
    })


@api_view(["POST"])
def driver_account(request, pk):
    denied = _need(request, "drivers", write=True)
    if denied:
        return denied
    d = get_object_or_404(Driver, pk=pk)
    action = request.data.get("action")
    try:
        if action == "create":
            driver_ops.create_login(d, request.user, request.data.get("username", ""), request.data.get("password", ""))
        elif action == "reset":
            driver_ops.reset_password(d, request.user, request.data.get("password", ""))
        elif action in ("activate", "deactivate"):
            driver_ops.set_active(d, request.user, action == "activate")
        else:
            return Response({"detail": "Unknown action."}, status=400)
    except driver_ops.OpError as e:
        return Response({"detail": str(e)}, status=400)
    d.refresh_from_db()
    return Response({"status": d.status, "account": {"username": d.user.username, "is_active": d.user.is_active}
                     if d.user_id else None})


@api_view(["POST"])
def assign_vehicle(request, pk):
    denied = _need(request, "drivers", write=True)
    if denied:
        return denied
    d = get_object_or_404(Driver, pk=pk)
    vid = require_id(request.data, "vehicle", required=False)
    vehicle = get_object_or_404(Vehicle, pk=vid) if vid else None
    try:
        driver_ops.assign_vehicle(d, request.user, vehicle, request.data.get("start_date") or None, request.data.get("notes", ""))
    except ValueError:
        return Response({"start_date": ["Use a date like 2026-10-01."]}, status=400)
    return Response({"vehicle": _vehicle(d.assigned_vehicle)})


@api_view(["GET", "POST"])
def assign_trip(request):
    denied = _need(request, "bookings", write=True)
    if denied:
        return denied
    if request.method == "POST":
        booking_id, driver_id = require_id(request.data, "booking"), require_id(request.data, "driver")
        booking = get_object_or_404(Booking, pk=booking_id)
        driver = get_object_or_404(Driver, pk=driver_id)
        try:
            driver_ops.assign_trip(booking, driver, request.user)
        except driver_ops.OpError as e:
            return Response({"detail": str(e)}, status=400)
        return Response(_trip(booking))
    return Response({"results": [_trip(b) for b in driver_ops.assignable_trips()[:80]],
                     "drivers": [{"id": d.pk, "name": d.name, "vehicle": d.assigned_vehicle.label if d.assigned_vehicle_id else None}
                                 for d in Driver.objects.exclude(status="inactive").select_related("assigned_vehicle")]})


# ------------------------------------------------------------------ activity & notifications
@api_view(["GET"])
def activity(request):
    denied = _need(request, "drivers")
    if denied:
        return denied
    check_query(request.query_params, ints=("driver",))
    qs = DriverActivity.objects.select_related("driver", "vehicle", "booking")
    if request.query_params.get("driver"):
        qs = qs.filter(driver_id=request.query_params["driver"])
    q = request.query_params.get("q")
    if q:
        qs = qs.filter(Q(text__icontains=q) | Q(booking__booking_number__icontains=q))
    page = Paginator(qs, 50).get_page(request.query_params.get("page"))
    return Response({"count": page.paginator.count, "next": page.next_page_number() if page.has_next() else None,
                     "results": [_activity(a) for a in page]})


@api_view(["GET"])
def notifications(request):
    rows = list(request.user.notifications.all()[:80])
    if request.query_params.get("mark_read", "1") == "1":
        request.user.notifications.filter(pk__in=[n.pk for n in rows if not n.is_read]).update(is_read=True)
    return Response({"results": [{"id": n.pk, "text": n.text, "url": n.url, "is_read": n.is_read,
                                  "created_at": n.created_at} for n in rows]})


@api_view(["GET"])
def unread(request):
    return Response({"unread": request.user.notifications.filter(is_read=False).count()})


# ------------------------------------------------------------------ generic resources (every module's list + form)
API_PATHS = {"users": "/api/auth/users/"}


def _plain(choices, request):
    ch = choices(request) if callable(choices) else (choices or [])
    return [[str(a), str(b)] for a, b in ch]


def _django(request):
    """The underlying Django request with the token-authenticated user, and the parsed body
    (multipart or JSON, for POST, PATCH and PUT) as .POST / .FILES for the resource helpers."""
    dj = request._request
    dj.user = request.user
    if request.method in ("POST", "PATCH", "PUT"):
        dj.POST = request.data
        dj._files = request.FILES
    return dj


def _defs(res, request):
    return {
        "key": res.key, "title": res.title, "subtitle": res.subtitle, "module": res.module, "item": res.item,
        "add_label": res.add_label, "search_placeholder": res.search_placeholder, "empty_text": res.empty_text,
        "period": bool(res.period), "default_period": res.default_period, "vehicle_filters": bool(res.vehicle_filters),
        "preview": res.preview, "writable": can(request.user, res.module, "write"),
        "fields": [{k: (_plain(v, request) if k == "choices" and f["kind"] == "select" else v)
                    for k, v in f.items() if k != "choices" or f["kind"] == "select"} for f in res.fields],
        "filters": [{**{k: v for k, v in f.items() if k != "choices"}, "choices": _plain(f["choices"], request)}
                    for f in res.filters],
        "defaults": {k: (str(v) if not isinstance(v, (bool, int, float)) else v) for k, v in res.defaults(request).items()},
    }


@api_view(["GET"])
def resource_defs(request):
    dj = _django(request)
    return Response({k: _defs(res, dj) for k, res in RESOURCES.items() if can(request.user, res.module)})


def _resource_or_403(request, key, write=False):
    res = RESOURCES.get(key)
    if res is None:
        return None, Response({"detail": "Not found."}, status=404)
    if not can(request.user, res.module, "write" if write else "read"):
        return None, Response({"detail": "Your role doesn't allow this."}, status=403)
    return res, None


def _row(res, obj, request):
    data = res.get_serializer_class()(obj, context={"request": request}).data
    locked = res.locked(obj)
    return {**data, "_locked": locked or None}


@api_view(["GET", "POST"])
def resource_list(request, key):
    res, err = _resource_or_403(request, key, write=request.method == "POST")
    if err:
        return err
    if request.method == "GET":
        check_query(request.query_params)
    dj = _django(request)
    if request.method == "POST":
        obj, errors, form_error = submit_resource(res, dj, None)
        if obj is None:
            return Response({"errors": errors, "detail": form_error}, status=400)
        return Response(_row(res, obj, dj), status=201)
    vs = viewset_for(res, dj)
    qs, period, totals = filtered_rows(res, vs, dj)
    try:
        size = min(int(request.query_params.get("page_size", 30)), 200)
    except ValueError:
        size = 30
    page = Paginator(qs, size).get_page(request.query_params.get("page"))
    count = page.paginator.count
    return Response({
        "count": count, "next": page.next_page_number() if page.has_next() else None, "period": period,
        "totals": [{"label": a, "value": b, "tone": c} for a, b, c in res.totals(totals, count)] if count else [],
        "results": [_row(res, o, dj) for o in page.object_list],
    })


@api_view(["GET", "POST", "PATCH", "DELETE"])
def resource_detail(request, key, pk):
    res, err = _resource_or_403(request, key, write=request.method != "GET")
    if err:
        return err
    dj = _django(request)
    vs = viewset_for(res, dj)
    obj = get_object_or_404(res.queryset(vs), pk=pk)
    if request.method == "GET":
        out = _row(res, obj, dj)
        if key == "bookings":
            out["_later_payments"] = float(max(obj.get_received() - (obj.advance or 0), 0))
        return Response(out)
    if request.method == "DELETE":
        blocked = delete_resource(res, vs, dj, obj)
        if blocked:
            return Response({"detail": blocked}, status=400)
        return Response(status=204)
    locked = res.locked(obj)
    if locked:
        return Response({"detail": locked}, status=400)
    saved, errors, form_error = submit_resource(res, dj, obj)
    if saved is None:
        return Response({"errors": errors, "detail": form_error}, status=400)
    return Response(_row(res, saved, dj))
