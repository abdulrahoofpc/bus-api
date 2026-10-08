"""Data for the office dashboard (/api/office/dashboard/): 'Today on the road', 'Needs attention' and driver numbers."""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.dateformat import format as dformat

from bookings.models import Booking
from common.dates import today
from finance.models import EMIInstallment, Expense, Maintenance
from common import links
from fleet.models import Driver, Vehicle

from .resource_ops import can


def _time(dt):
    """10:56 AM — Django formatting works the same on Windows, Linux and Mac; strftime's no-padding codes fail on Windows."""
    return dformat(timezone.localtime(dt), "g:i A")


def _day(d, weekday=False):
    """3 Oct, or Sat 3 Oct."""
    return dformat(d, "D j M" if weekday else "j M")


def _progress(b, now):
    """Rough share of the trip already done, from start time to the planned return evening."""
    tz = timezone.get_current_timezone()
    start = b.started_at or timezone.make_aware(datetime.combine(b.trip_date, time(6, 0)), tz)
    end = timezone.make_aware(datetime.combine(b.return_date or b.trip_date, time(20, 0)), tz)
    if end <= start:
        end = start + timedelta(hours=12)
    share = (now - start).total_seconds() / (end - start).total_seconds()
    return max(6, min(94, round(share * 100)))


def road_board():
    """One row per vehicle: on the road now, next trip soon, in the workshop, or free."""
    t, now = today(), timezone.now()
    ongoing = {b.vehicle_id: b for b in Booking.objects.filter(trip_status="ongoing").select_related("driver", "customer")}
    upcoming = {}
    for b in Booking.objects.filter(trip_status="upcoming", trip_date__gte=t, trip_date__lte=t + timedelta(days=3)) \
            .select_related("driver", "customer").order_by("trip_date", "id"):
        upcoming.setdefault(b.vehicle_id, b)
    rows = []
    for v in Vehicle.objects.exclude(status="sold").select_related("vehicle_type").prefetch_related("drivers"):
        b = ongoing.get(v.pk)
        if b:
            state, order = "road", 0
        elif upcoming.get(v.pk):
            b = upcoming[v.pk]
            state, order = "next", 1 if b.trip_date == t else 2
        else:
            state, order = ("workshop", 4) if v.status == "maintenance" else ("free", 3)
        driver = (b.driver if b and b.driver_id else None) or next(iter(v.drivers.all()), None)
        row = {"v": v, "state": state, "order": order, "trip": b, "driver": driver}
        if b and state == "road":
            row["progress"] = _progress(b, now)
            row["when"] = f"Started {_time(b.started_at)}" if b.started_at else "On the road"
            if b.return_date and b.return_date > t:
                row["when"] += f", back {_day(b.return_date, weekday=True)}"
        elif b:
            row["when"] = "Today" if b.trip_date == t else ("Tomorrow" if b.trip_date == t + timedelta(days=1) else _day(b.trip_date, weekday=True))
            row["when"] += ", accepted" if b.accepted_at else (", waiting for driver" if b.driver_id else ", no driver yet")
        rows.append(row)
    rows.sort(key=lambda r: (r["order"], r["trip"].trip_date if r["trip"] else t, r["v"].registration_number))
    counts = {k: sum(1 for r in rows if r["state"] == k) for k in ("road", "next", "free", "workshop")}
    return rows, counts


def attention(user, d):
    """Everything that needs a decision or a payment, most urgent first."""
    t = today()
    items = []
    if can(user, "expenses", "write"):
        pend = Expense.all_objects.filter(status="pending").aggregate(n=Count("id"), s=Sum("amount"))
        if pend["n"]:
            names = ", ".join(sorted(set(Expense.all_objects.filter(status="pending", driver__isnull=False)
                                         .values_list("driver__name", flat=True)))[:3])
            items.append({"key": "expense_review", "tone": "amber", "icon": "out", "title": f"{pend['n']} driver expense{'s' if pend['n'] != 1 else ''} to approve",
                          "detail": f"₹{pend['s']:,.0f} from {names or 'the office'}", "url": links.expense_review(), "cta": "Review"})
    if can(user, "maintenance"):
        reports = Maintenance.objects.filter(status__in=["reported", "under_review"]).select_related("vehicle")
        if reports:
            rank = {"emergency": 0, "high": 1, "medium": 2, "low": 3}
            top = sorted(reports, key=lambda m: rank.get(m.priority, 9))[0]
            n = len(reports)
            items.append({"key": "maintenance_reported", "tone": "red" if top.priority in ("emergency", "high") else "amber", "icon": "wrench",
                          "title": f"{n} problem report{'s' if n != 1 else ''} from drivers",
                          "detail": f"{top.get_problem_category_display() or 'Problem'} on {top.vehicle.label}, {top.get_priority_display().lower()} priority",
                          "url": links.resource_list("maintenance", "status=reported"), "cta": "Open"})
    if can(user, "finance"):
        overdue = EMIInstallment.objects.filter(paid_date__isnull=True, due_date__lt=t).aggregate(n=Count("id"), s=Sum("amount"))
        if overdue["n"]:
            items.append({"key": "emi_overdue", "tone": "red", "icon": "bank", "title": f"{overdue['n']} EMI{'s' if overdue['n'] != 1 else ''} overdue",
                          "detail": f"₹{overdue['s']:,.0f} past the due date", "url": links.finance("status=overdue"), "cta": "Pay"})
    urgent_docs = [x for x in d["documents"] if x["status"] in ("expired", "7_days")]
    if urgent_docs:
        first = urgent_docs[0]
        items.append({"key": "documents_attention", "tone": "red" if any(x["status"] == "expired" for x in urgent_docs) else "amber", "icon": "file",
                      "title": f"{len(urgent_docs)} document{'s' if len(urgent_docs) != 1 else ''} expired or expiring this week",
                      "detail": f"{first['item']} for {first['vehicle']}, {'expired' if first['days_left'] < 0 else 'expires'} {_day(first['date'])}",
                      "url": links.resource_list("documents", "status=attention"), "cta": "Renew"})
    due_service = [s for s in d["service_due"] if s["days_left"] <= 7]
    if due_service:
        s = due_service[0]
        items.append({"key": f"vehicle:{s['vehicle_id']}", "tone": "amber", "icon": "wrench", "title": f"Service due for {len(due_service)} vehicle{'s' if len(due_service) != 1 else ''}",
                      "detail": f"{s['vehicle']}, {_day(s['date'])}", "url": links.vehicle(s["vehicle_id"], "tab=maintenance"),
                      "cta": "Plan"})
    unassigned = Booking.objects.filter(trip_status="upcoming", driver__isnull=True, trip_date__gte=t, trip_date__lte=t + timedelta(days=7)).count()
    if unassigned and can(user, "bookings", "write"):
        items.append({"key": "assign_trip", "tone": "blue", "icon": "users", "title": f"{unassigned} trip{'s' if unassigned != 1 else ''} this week without a driver",
                      "detail": "Drivers get a notification as soon as you assign them", "url": links.assign_trip(), "cta": "Assign"})
    f = d.get("finance")
    if f and f["pending_payments"]["count"]:
        items.append({"key": "bookings_due", "tone": "blue", "icon": "in", "title": f"{f['pending_payments']['count']} bookings with money to collect",
                      "detail": f"₹{f['pending_payments']['total']:,.0f} still to receive from customers",
                      "url": links.resource_list("bookings", "payment_status=due"), "cta": "See"})
    order = {"red": 0, "amber": 1, "blue": 2}
    items.sort(key=lambda x: order[x["tone"]])
    return items


def ledger(f):
    """This month's money as proportional bars (income is the full width)."""
    m = f["month"]
    top = max(m["income"], m["expenses"] + m["emi"], Decimal("1"))
    pct = lambda x: max(1, round(x / top * 100)) if x else 0  # noqa: E731
    return {"income": pct(m["income"]), "expenses": pct(m["expenses"]), "emi": pct(m["emi"]),
            "margin": round(m["profit"] / m["income"] * 100) if m["income"] else None}


def driver_overview():
    """Driver numbers for the office dashboard."""
    from finance.models import FuelEntry
    t = today()
    drivers = Driver.objects.all()
    on_trip = Booking.objects.filter(trip_status="ongoing", driver__isnull=False).values("driver").distinct().count()
    active = drivers.filter(status="active").count()
    return {
        "total": drivers.count(), "active": active, "on_trip": on_trip, "available": max(active - on_trip, 0),
        "pending_expenses": Expense.all_objects.filter(status="pending").count(),
        "pending_maintenance": Maintenance.objects.filter(status__in=["reported", "under_review"]).count(),
        "today_trips": Booking.objects.filter(driver__isnull=False, trip_date=t).exclude(trip_status="cancelled").count(),
        "today_fuel": FuelEntry.objects.filter(date=t, driver__isnull=False).count(),
    }
