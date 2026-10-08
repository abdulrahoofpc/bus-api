"""
Driver app support: an activity log of every important driver action, and
simple in-app notifications for drivers and office users.
"""
from django.conf import settings
from django.db import models


class DriverActivity(models.Model):
    driver = models.ForeignKey("fleet.Driver", on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    action = models.CharField(max_length=40)          # e.g. trip_started, fuel_added, expense_approved
    text = models.CharField(max_length=255)           # "John started trip BK-2026-0012"
    vehicle = models.ForeignKey("fleet.Vehicle", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    booking = models.ForeignKey("bookings.Booking", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "driver activities"


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    text = models.CharField(max_length=255)
    url = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
