from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        ACCOUNTANT = "accountant", "Accountant"
        MANAGER = "manager", "Manager"
        STAFF = "staff", "Staff"
        DRIVER = "driver", "Driver"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    phone = models.CharField(max_length=20, blank=True)

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = self.Role.ADMIN
        super().save(*args, **kwargs)

    @property
    def is_driver(self):
        return self.role == self.Role.DRIVER and not self.is_superuser

    @property
    def display_name(self):
        return self.get_full_name() or self.username


class BusinessSettings(models.Model):
    """Single row holding the business details printed on reports."""

    name = models.CharField(max_length=150, default="My Travels")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    gst_number = models.CharField(max_length=30, blank=True)

    class Meta:
        verbose_name_plural = "Business settings"

    def __str__(self):
        return self.name

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
