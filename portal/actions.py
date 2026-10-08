"""
Everything a driver can do, in one place. The driver API (portal/api.py) calls
these functions, so the rules (stage checks, finance links, activity log,
notifications) live in one place.

Each function takes the driver and already-validated data (from portal.forms).
A rule violation raises ActionError with a message for the driver.
"""
from django.db import transaction
from django.utils import timezone

from bookings.models import Booking
from common import links
from finance.models import Expense, FuelEntry, Maintenance

from . import services
from .forms import maintenance_kind


class ActionError(Exception):
    pass


def _booking_link(trip):
    return links.resource_list("bookings", f"search={trip.booking_number}")


# ------------------------------------------------------------------ trips
def accept_trip(driver, user, trip):
    if trip.driver_stage != "assigned":
        raise ActionError("This trip can't be accepted now.")
    trip.accepted_at = timezone.now()
    trip.save(update_fields=["accepted_at"])
    services.log(driver, "trip_accepted", f"{driver.name} accepted trip {trip.booking_number}", user,
                 trip.vehicle, trip, status="accepted")
    services.notify_office("bookings", f"{driver.name} accepted trip {trip.booking_number}", _booking_link(trip))
    return trip


def check_can_start(driver, trip):
    if trip.driver_stage not in ("assigned", "accepted"):
        raise ActionError("This trip has already started or finished.")
    other = Booking.objects.filter(driver=driver, trip_status="ongoing").exclude(pk=trip.pk).first()
    if other:
        raise ActionError(f"Finish trip {other.booking_number} before starting another one.")


def start_trip(driver, user, trip, data):
    check_can_start(driver, trip)
    with transaction.atomic():
        trip.start_odometer, trip.start_location, trip.started_at = data["start_odometer"], data["start_location"], data["started_at"]
        trip.accepted_at = trip.accepted_at or data["started_at"]
        trip.trip_status = "ongoing"            # existing booking logic marks the vehicle On Trip
        trip.save()
        services.log(driver, "trip_started", f"{driver.name} started trip {trip.booking_number}", user,
                     trip.vehicle, trip, status="ongoing")
    services.notify_office("bookings", f"{driver.name} started trip {trip.booking_number} ({trip.vehicle.label})",
                           _booking_link(trip))
    return trip


def complete_trip(driver, user, trip, data):
    if trip.trip_status != "ongoing":
        raise ActionError("Start the trip before completing it.")
    with transaction.atomic():
        trip.end_odometer, trip.end_location, trip.completed_at = data["end_odometer"], data["end_location"], data["completed_at"]
        trip.trip_notes = "\n".join(x for x in [trip.trip_notes, data.get("trip_notes")] if x)
        if data.get("trip_document"):
            trip.trip_document = data["trip_document"]
        trip.trip_status = "completed"          # existing booking logic frees the vehicle
        trip.save()
        km = trip.total_km
        services.log(driver, "trip_completed",
                     f"{driver.name} completed trip {trip.booking_number}" + (f" · {km} km" if km is not None else ""),
                     user, trip.vehicle, trip, status="completed")
        if data.get("extra_category") and data.get("extra_amount"):
            submit_expense(driver, user, date=timezone.localdate(), vehicle=trip.vehicle, booking=trip,
                           category=data["extra_category"], amount=data["extra_amount"],
                           description=f"Added when completing {trip.booking_number}", receipt=data.get("extra_receipt"))
    services.notify_office("bookings", f"{driver.name} completed trip {trip.booking_number}", _booking_link(trip))
    return trip


def update_trip(driver, user, trip, data):
    if trip.trip_status in ("completed", "cancelled"):
        raise ActionError("Finished trips can't be changed. Ask the office if something is wrong.")
    trip.trip_notes = data.get("trip_notes", "")
    if data.get("trip_document"):
        trip.trip_document = data["trip_document"]
    trip.save(update_fields=["trip_notes", "trip_document"])
    services.log(driver, "trip_updated", f"{driver.name} updated trip {trip.booking_number}", user, trip.vehicle, trip)
    return trip


# ------------------------------------------------------------------ fuel, expenses, problems
def add_fuel(driver, user, data):
    entry = FuelEntry.objects.create(
        date=data["date"], vehicle=data["vehicle"], driver=driver, booking=data.get("booking"), fuel_type=data["fuel_type"],
        litres=data["litres"], rate=data["rate"], odometer=data.get("odometer"), fuel_station=data.get("fuel_station", ""),
        payment_method=data["payment_method"], receipt=data.get("receipt"), notes=data.get("notes", ""),
        submitted_by=user)          # FuelEntry.save(): Total = Litres × Rate, and the linked expense is created
    services.log(driver, "fuel_added", f"{driver.name} added fuel {entry.litres} L · ₹{entry.total_amount:,.2f}".replace(".00", ""),
                 user, entry.vehicle, entry.booking, entry.total_amount)
    services.notify_office("fuel", f"New fuel entry by {driver.name}: {entry.vehicle.label}, ₹{entry.total_amount:,.0f}",
                           links.resource_list("fuel"))
    return entry


def submit_expense(driver, user, **values):
    receipt = values.pop("receipt", None)
    exp = Expense.all_objects.create(status="pending", driver=driver, submitted_by=user,
                                     payment_method=values.pop("payment_method", "cash"), receipt=receipt, **values)
    services.log(driver, "expense_submitted",
                 f"{driver.name} submitted {exp.get_category_display().lower()} expense ₹{exp.amount:,.0f}",
                 user, exp.vehicle, exp.booking, exp.amount, "pending")
    services.notify_office("expenses", f"{driver.name} submitted an expense of ₹{exp.amount:,.0f} for approval",
                           links.expense_review())
    return exp


def add_expense(driver, user, data):
    return submit_expense(driver, user, date=data["date"], vehicle=data["vehicle"], booking=data.get("booking"),
                          category=data["category"], amount=data["amount"], description=data.get("description", ""),
                          payment_method=data["payment_method"], receipt=data.get("receipt"), notes=data.get("notes", ""))


def withdraw_expense(driver, user, exp):
    if exp.status != "pending":
        raise ActionError("Only pending expenses can be withdrawn.")
    services.log(driver, "expense_withdrawn", f"{driver.name} withdrew an expense of ₹{exp.amount:,.0f}",
                 user, exp.vehicle, exp.booking, exp.amount, "withdrawn")
    exp.delete()


def report_problem(driver, user, data):
    m = Maintenance.objects.create(
        vehicle=data["vehicle"], date=data["date"], maintenance_type=maintenance_kind(data["problem_category"]),
        problem_category=data["problem_category"], priority=data["priority"], description=data["description"],
        odometer=data.get("odometer"), photo=data.get("photo"), notes=data.get("notes", ""),
        status="reported", driver=driver, reported_by=user)       # no cost until the office marks it Completed
    services.log(driver, "problem_reported",
                 f"{driver.name} reported a {m.get_problem_category_display().lower()} problem ({m.get_priority_display()})",
                 user, m.vehicle, status="reported")
    prefix = "🚨 EMERGENCY: " if m.priority == "emergency" else ""
    services.notify_office("maintenance", f"{prefix}{driver.name} reported a problem on {m.vehicle.label}",
                           links.resource_edit("maintenance", m.pk))
    return m


def last_odometer(vehicle):
    return FuelEntry.objects.filter(vehicle=vehicle, odometer__isnull=False).order_by("-date", "-id") \
        .values_list("odometer", flat=True).first()


# ------------------------------------------------------------------ dashboard numbers
def dashboard_data(driver):
    from django.db.models import Q, Sum
    t = timezone.localdate()
    month_start = t.replace(day=1)
    trips = Booking.objects.filter(driver=driver).select_related("customer", "vehicle")
    current = trips.filter(trip_status="ongoing").order_by("trip_date").first()
    today_trips = trips.filter(trip_date__lte=t).filter(Q(return_date__gte=t) | Q(return_date__isnull=True, trip_date=t)) \
        .exclude(trip_status="cancelled")
    next_trip = current or today_trips.exclude(trip_status="completed").order_by("trip_date").first() or \
        trips.filter(trip_status="upcoming", trip_date__gte=t).order_by("trip_date").first()
    pending_expenses = Expense.all_objects.filter(driver=driver, status="pending").count()
    open_reports = Maintenance.objects.filter(driver=driver).exclude(status__in=["completed", "rejected"]).count()
    done_this_month = trips.filter(trip_status="completed", trip_date__gte=month_start)
    later = trips.filter(trip_status="upcoming", trip_date__gte=t).order_by("trip_date", "id")
    if next_trip:
        later = later.exclude(pk=next_trip.pk)
    return {
        "trip": next_trip,
        "new_trips": trips.filter(trip_status="upcoming", accepted_at__isnull=True).count(),
        "to_accept": list(later.filter(accepted_at__isnull=True)[:5]),
        "coming_up": list(later.filter(accepted_at__isnull=False)[:3]),
        "km_month": sum((b.total_km or 0) for b in done_this_month),
        "stats": {
            "today_trips": today_trips.count(),
            "month_trips": trips.filter(trip_date__gte=month_start, trip_date__lte=t).exclude(trip_status="cancelled").count(),
            "fuel_entries": FuelEntry.objects.filter(driver=driver, date__gte=month_start).count(),
            "expenses": Expense.all_objects.filter(driver=driver, date__gte=month_start).exclude(status="rejected")
                                .aggregate(s=Sum("amount"))["s"] or 0,
            "pending_expenses": pending_expenses,
            "open_reports": open_reports,
            "pending": pending_expenses + open_reports,
        },
    }


# ------------------------------------------------------------------ profile
PROFILE_LABELS = {"photo": "photo", "phone": "mobile number", "email": "email", "address": "address",
                  "blood_group": "blood group", "emergency_contact_name": "emergency contact",
                  "emergency_contact_phone": "emergency contact", "licence_number": "licence number",
                  "licence_expiry": "licence expiry"}


def update_profile(driver, user, form):
    """Save a validated ProfileForm, keep the login account in step, and tell the office what changed."""
    changed = [f for f in form.changed_data if f in PROFILE_LABELS]
    if not changed:
        return driver, []
    with transaction.atomic():
        driver = form.save()
        user.email, user.phone = driver.email, driver.phone
        user.save(update_fields=["email", "phone"])
    what = list(dict.fromkeys(PROFILE_LABELS[f] for f in changed))
    text = ", ".join(what[:-1]) + (" and " if len(what) > 1 else "") + what[-1]
    services.log(driver, "profile_updated", f"{driver.name} updated their {text}", user)
    if any(f.startswith("licence") or f == "phone" for f in changed):
        services.notify_office("drivers", f"{driver.name} updated their {text}", links.driver(driver.pk))
    return driver, what
