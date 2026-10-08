"""Logging and notifications used by both portals."""
from django.contrib.auth import get_user_model

from accounts.permissions import access_for

from .models import DriverActivity, Notification


def log(driver, action, text, actor=None, vehicle=None, booking=None, amount=None, status=""):
    return DriverActivity.objects.create(driver=driver, actor=actor, action=action, text=text, vehicle=vehicle,
                                         booking=booking, amount=amount, status=status)


def notify_user(user, text, url=""):
    if user and user.is_active:
        Notification.objects.create(user=user, text=text, url=url)


def notify_driver(driver, text, url=""):
    if driver and driver.user_id:
        notify_user(driver.user, text, url)


def notify_office(module, text, url=""):
    """Tell every active office user who can work in `module` (e.g. expenses, maintenance, bookings)."""
    User = get_user_model()
    for user in User.objects.filter(is_active=True).exclude(role="driver", is_superuser=False):
        if access_for(user).get(module) == "rw":
            Notification.objects.create(user=user, text=text, url=url)
