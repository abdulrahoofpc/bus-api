"""
Office actions on drivers, in one place: create/reset/disable logins, assign
vehicles and trips, approve or reject expenses. Used by /api/office/ (office/api.py),
so the rules, activity log and notifications always match. Problems raise OpError with a friendly message.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from bookings.models import Booking
from common.dates import today
from portal import services as ps

User = get_user_model()


class OpError(Exception):
    pass


def _check_password(password, user=None):
    if len(password or "") < 8:
        raise OpError("The password needs at least 8 characters.")
    try:
        validate_password(password, user)
    except ValidationError as e:
        raise OpError(" ".join(e.messages))


def create_login(driver, by, username, password):
    if driver.user_id:
        raise OpError("This driver already has a login.")
    username = (username or "").strip()
    if not username:
        raise OpError("Enter a username (the driver's phone number works well).")
    if User.objects.filter(username__iexact=username).exists():
        raise OpError("That username is already taken.")
    _check_password(password)
    user = User.objects.create_user(username=username, password=password, first_name=driver.name, phone=driver.phone,
                                    email=driver.email, role="driver", is_active=driver.status != "inactive")
    driver.user = user
    driver.save(update_fields=["user"])
    ps.log(driver, "account_created", f"{by.display_name} created an app login for {driver.name}", by)
    return user


def reset_password(driver, by, password):
    if not driver.user_id:
        raise OpError("This driver has no login yet.")
    _check_password(password, driver.user)
    driver.user.set_password(password)
    driver.user.save()
    ps.log(driver, "password_reset", f"{by.display_name} reset {driver.name}'s password", by)
    ps.notify_driver(driver, "Your password was reset by the office.")


def set_active(driver, by, active):
    if not driver.user_id:
        raise OpError("This driver has no login yet.")
    with transaction.atomic():
        User.objects.filter(pk=driver.user_id).update(is_active=active)
        driver.status = "active" if active else "inactive"
        driver.save(update_fields=["status"])
    word = "activated" if active else "deactivated"
    ps.log(driver, f"account_{word}", f"{by.display_name} {word} {driver.name}", by, status=driver.status)


def assign_vehicle(driver, by, vehicle, start=None, notes=""):
    if isinstance(start, str):
        start = date.fromisoformat(start) if start else None
    driver.assign_vehicle(vehicle, by=by, start=start or today(), notes=notes or "")
    if vehicle:
        ps.log(driver, "vehicle_assigned", f"{driver.name} assigned to {vehicle.label}", by, vehicle)
        ps.notify_driver(driver, f"You have been assigned vehicle {vehicle.label}", "/driver/vehicle/")
    else:
        ps.log(driver, "vehicle_unassigned", f"{driver.name} no longer has a vehicle", by)


def assign_trip(booking, driver, by):
    if booking.trip_status not in ("upcoming", "ongoing"):
        raise OpError("Only upcoming or ongoing trips can be assigned.")
    previous = booking.driver
    booking.driver = driver
    booking.accepted_at = None
    booking.save()
    ps.log(driver, "trip_assigned", f"Trip {booking.booking_number} assigned to {driver.name}", by,
           booking.vehicle, booking, status="assigned")
    ps.notify_driver(driver, f"New trip assigned: {booking.booking_number} on {booking.trip_date:%d %b} "
                             f"({booking.destination or booking.customer.name})", f"/driver/trips/{booking.pk}/")
    if previous and previous != driver:
        ps.notify_driver(previous, f"Trip {booking.booking_number} is no longer assigned to you")
    return booking


def decide_expense(exp, by, action, reason=""):
    if exp.status != "pending":
        raise OpError("This expense was already reviewed.")
    if action not in ("approve", "reject"):
        raise OpError("Choose approve or reject.")
    reason = (reason or "").strip()
    if action == "reject" and not reason:
        raise OpError("Please give a reason for rejecting the expense.")
    exp.status = "approved" if action == "approve" else "rejected"
    exp.rejection_reason = reason if action == "reject" else ""
    exp.reviewed_by, exp.reviewed_at = by, timezone.now()
    exp.save()
    word = "approved" if action == "approve" else "rejected"
    if exp.driver_id:
        ps.log(exp.driver, f"expense_{word}", f"{by.display_name} {word} expense ₹{exp.amount:,.0f}",
               by, exp.vehicle, exp.booking, exp.amount, exp.status)
        ps.notify_driver(exp.driver, f"Your {exp.get_category_display().lower()} expense of ₹{exp.amount:,.0f} was {word}"
                         + (f": {reason}" if reason else "."), "/driver/expenses/")
    return exp


def assignable_trips():
    return Booking.objects.filter(trip_status="upcoming", trip_date__gte=today()) \
        .select_related("customer", "vehicle", "driver").order_by("trip_date")
