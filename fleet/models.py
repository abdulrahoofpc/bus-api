
from django.conf import settings
from django.db import models

from common.choices import FuelType
from common.dates import today


class VehicleType(models.Model):
    """
    Vehicle types are data, not code. Bus, Taxi, Car, Van, SUV and Other are
    created by a migration; more can be added from Settings at any time.
    """

    name = models.CharField(max_length=60, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Vehicle(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        ON_TRIP = "on_trip", "On Trip"
        MAINTENANCE = "maintenance", "Maintenance"
        INACTIVE = "inactive", "Inactive"
        SOLD = "sold", "Sold"

    registration_number = models.CharField(max_length=20, unique=True)
    short_name = models.CharField(
        max_length=40, blank=True,
        help_text="Easy name used in lists and reports, e.g. 'Bus 1' or 'Innova White'.",
    )
    vehicle_type = models.ForeignKey(VehicleType, on_delete=models.PROTECT, related_name="vehicles")
    brand = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=60, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    seating_capacity = models.PositiveSmallIntegerField(null=True, blank=True)
    fuel_type = models.CharField(max_length=20, choices=FuelType.choices, default=FuelType.DIESEL)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["vehicle_type__sort_order", "short_name", "registration_number"]

    def save(self, *args, **kwargs):
        self.registration_number = self.registration_number.upper().strip()
        super().save(*args, **kwargs)

    @property
    def label(self):
        if self.short_name:
            return f"{self.short_name} ({self.registration_number})"
        return self.registration_number

    def __str__(self):
        return self.label


def expiry_status(expiry_date):
    """expired / 7_days / 30_days / valid — shared by documents and licences."""
    if not expiry_date:
        return "unknown"
    days = (expiry_date - today()).days
    if days < 0:
        return "expired"
    if days <= 7:
        return "7_days"
    if days <= 30:
        return "30_days"
    return "valid"


EXPIRY_LABELS = {
    "expired": "Expired",
    "7_days": "Expires in 7 days",
    "30_days": "Expires in 30 days",
    "valid": "Valid",
    "unknown": "No date",
}


class VehicleDocument(models.Model):
    class DocType(models.TextChoices):
        INSURANCE = "insurance", "Insurance"
        PERMIT = "permit", "Permit"
        FITNESS = "fitness", "Fitness Certificate"
        ROAD_TAX = "road_tax", "Road Tax"
        POLLUTION = "pollution", "Pollution Certificate"
        RC = "rc", "Registration (RC)"
        OTHER = "other", "Other Document"

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=20, choices=DocType.choices)
    document_number = models.CharField(max_length=60, blank=True)
    provider = models.CharField(max_length=100, blank=True, help_text="Insurer / issuing office")
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    file = models.FileField(upload_to="documents/%Y/%m/", null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["expiry_date"]

    @property
    def status(self):
        return expiry_status(self.expiry_date)

    @property
    def days_left(self):
        return (self.expiry_date - today()).days

    def __str__(self):
        return f"{self.get_doc_type_display()} - {self.vehicle}"


class Driver(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ON_LEAVE = "on_leave", "On Leave"
        INACTIVE = "inactive", "Inactive"

    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True)
    licence_number = models.CharField(max_length=40, blank=True)
    licence_expiry = models.DateField(null=True, blank=True)
    salary = models.DecimalField("Monthly salary", max_digits=12, decimal_places=2, default=0)
    joining_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    assigned_vehicle = models.ForeignKey(
        Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name="drivers",
        help_text="The vehicle the driver uses now. Past vehicles are kept in VehicleAssignment.",
    )
    email = models.EmailField(blank=True)
    # Personal details the driver can keep up to date from the driver app
    photo = models.FileField(upload_to="drivers/", null=True, blank=True)
    address = models.TextField(blank=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    blood_group = models.CharField(max_length=3, blank=True, choices=[
        (g, g) for g in ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")])
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="driver_profile",
        help_text="Login account for the Driver Portal.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def assign_vehicle(self, vehicle, by=None, start=None, notes=""):
        """Close the current assignment (if any) and start a new one. Keeps full history."""
        from common.dates import today as _today
        start = start or _today()
        open_rows = self.assignments.filter(end_date__isnull=True)
        if vehicle and open_rows.filter(vehicle=vehicle).exists():
            return open_rows.filter(vehicle=vehicle).first()
        open_rows.update(end_date=start)
        row = None
        if vehicle:
            row = VehicleAssignment.objects.create(driver=self, vehicle=vehicle, start_date=start,
                                                   assigned_by=by, notes=notes)
        Driver.objects.filter(pk=self.pk).update(assigned_vehicle=vehicle)
        self.assigned_vehicle = vehicle
        return row

    @property
    def licence_status(self):
        return expiry_status(self.licence_expiry)

    def __str__(self):
        return self.name


class VehicleAssignment(models.Model):
    """Which driver used which vehicle, and when. A driver can use many vehicles over time and vice versa."""

    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="assignments")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="assignments")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]

    @property
    def is_current(self):
        return self.end_date is None
