from decimal import Decimal

from django.db import models, transaction
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from common.choices import PaymentMethod
from common.dates import today
from fleet.models import Driver, Vehicle

MONEY = DecimalField(max_digits=14, decimal_places=2)


class Customer(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    company_name = models.CharField(max_length=150, blank=True)
    gst_number = models.CharField("GST number", max_length=20, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.company_name})" if self.company_name else self.name


def received_subquery(booking_ref="pk"):
    """Total money received for a booking (all income entries linked to it)."""
    from finance.models import Income

    return Coalesce(
        Subquery(
            Income.objects.filter(booking=OuterRef(booking_ref))
            .values("booking")
            .annotate(total=Sum("amount"))
            .values("total")[:1],
            output_field=MONEY,
        ),
        Value(Decimal("0")),
        output_field=MONEY,
    )


class BookingQuerySet(models.QuerySet):
    def with_totals(self):
        return self.annotate(received=received_subquery())


class Booking(models.Model):
    class TripStatus(models.TextChoices):
        UPCOMING = "upcoming", "Upcoming"
        ONGOING = "ongoing", "Ongoing"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    booking_number = models.CharField(max_length=20, unique=True, editable=False)
    booking_date = models.DateField(default=today, help_text="Date the booking was taken (advance date).")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="bookings")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="bookings")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name="bookings")
    trip_date = models.DateField()
    return_date = models.DateField(null=True, blank=True)
    pickup_location = models.CharField(max_length=150, blank=True)
    destination = models.CharField(max_length=150, blank=True)
    number_of_days = models.PositiveSmallIntegerField(default=1)
    booking_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    advance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    advance_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    trip_status = models.CharField(max_length=20, choices=TripStatus.choices, default=TripStatus.UPCOMING)
    notes = models.TextField(blank=True)
    # Driver app: accept → start → complete
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    start_odometer = models.PositiveIntegerField(null=True, blank=True)
    start_location = models.CharField(max_length=150, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    end_odometer = models.PositiveIntegerField(null=True, blank=True)
    end_location = models.CharField(max_length=150, blank=True)
    trip_notes = models.TextField("Driver's trip notes", blank=True)
    trip_document = models.FileField(upload_to="trips/%Y/%m/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = BookingQuerySet.as_manager()

    class Meta:
        ordering = ["-trip_date", "-id"]

    def __str__(self):
        return f"{self.booking_number} - {self.customer.name}"

    # ---- money -------------------------------------------------------------
    @property
    def total_km(self):
        """Total KM = Ending odometer − Starting odometer."""
        if self.start_odometer is not None and self.end_odometer is not None and self.end_odometer >= self.start_odometer:
            return self.end_odometer - self.start_odometer
        return None

    @property
    def duration(self):
        if self.started_at and self.completed_at and self.completed_at >= self.started_at:
            return self.completed_at - self.started_at
        return None

    @property
    def duration_text(self):
        d = self.duration
        if not d:
            return ""
        hours = int(d.total_seconds() // 3600)
        days, hours = divmod(hours, 24)
        mins = int(d.total_seconds() % 3600 // 60)
        parts = ([f"{days} day{'s' if days != 1 else ''}"] if days else []) + ([f"{hours} h"] if hours else [])
        return " ".join(parts + ([f"{mins} min"] if not days and mins else [])) or "Under a minute"

    @property
    def driver_stage(self):
        """Stage as the driver sees it: assigned → accepted → ongoing → completed (or cancelled)."""
        if self.trip_status == self.TripStatus.CANCELLED:
            return "cancelled"
        if self.trip_status == self.TripStatus.COMPLETED:
            return "completed"
        if self.trip_status == self.TripStatus.ONGOING:
            return "ongoing"
        return "accepted" if self.accepted_at else "assigned"

    DRIVER_STAGE_LABELS = {"assigned": "Assigned", "accepted": "Accepted", "ongoing": "Ongoing",
                           "completed": "Completed", "cancelled": "Cancelled"}

    @property
    def driver_stage_label(self):
        return self.DRIVER_STAGE_LABELS[self.driver_stage]

    def get_received(self):
        if hasattr(self, "received"):
            return self.received
        return self.incomes.aggregate(t=Sum("amount"))["t"] or Decimal("0")

    @property
    def balance(self):
        """Balance = Booking Amount - money received (starts as the advance)."""
        if self.trip_status == self.TripStatus.CANCELLED:
            return Decimal("0")
        return max(self.booking_amount - self.get_received(), Decimal("0"))

    @property
    def payment_status(self):
        received = self.get_received()
        if self.booking_amount > 0 and received >= self.booking_amount:
            return "paid"
        if received > 0:
            return "partial"
        return "pending"

    # ---- saving ------------------------------------------------------------
    def _next_number(self):
        year = (self.booking_date or today()).year
        prefix = f"BK-{year}-"
        last = (
            Booking.objects.filter(booking_number__startswith=prefix)
            .order_by("-booking_number").values_list("booking_number", flat=True).first()
        )
        seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
        return f"{prefix}{seq:04d}"

    def save(self, *args, **kwargs):
        if self.return_date and self.trip_date:
            self.number_of_days = max((self.return_date - self.trip_date).days + 1, 1)
        previous = None
        if self.pk:
            previous = Booking.objects.filter(pk=self.pk).values("trip_status", "vehicle_id").first()
        with transaction.atomic():
            if not self.booking_number:
                self.booking_number = self._next_number()
            super().save(*args, **kwargs)
            self._sync_advance_income()
            self._sync_vehicle_status(previous)

    def _sync_advance_income(self):
        """The advance is stored as an Income entry so monthly income is always complete."""
        from finance.models import Income

        entry = Income.objects.filter(booking=self, is_booking_advance=True).first()
        if self.advance and self.advance > 0:
            entry = entry or Income(booking=self, is_booking_advance=True)
            entry.date = self.booking_date
            entry.vehicle = self.vehicle
            entry.customer = self.customer
            entry.income_type = Income.IncomeType.ADVANCE
            entry.amount = self.advance
            entry.payment_method = self.advance_method
            entry.reference = self.booking_number
            entry.notes = f"Advance for booking {self.booking_number}"
            entry.save()
        elif entry:
            entry.delete()

    def _sync_vehicle_status(self, previous):
        vehicle = self.vehicle
        if self.trip_status == self.TripStatus.ONGOING:
            if vehicle.status in (Vehicle.Status.AVAILABLE, Vehicle.Status.ON_TRIP):
                Vehicle.objects.filter(pk=vehicle.pk).update(status=Vehicle.Status.ON_TRIP)
        # Free vehicles that no longer have an ongoing trip
        vehicle_ids = {vehicle.pk}
        if previous:
            vehicle_ids.add(previous["vehicle_id"])
        for vid in vehicle_ids:
            still_busy = Booking.objects.filter(vehicle_id=vid, trip_status=self.TripStatus.ONGOING).exists()
            if not still_busy:
                Vehicle.objects.filter(pk=vid, status=Vehicle.Status.ON_TRIP).update(status=Vehicle.Status.AVAILABLE)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            self.incomes.filter(is_booking_advance=True).delete()
            vehicle_id = self.vehicle_id
            result = super().delete(*args, **kwargs)
            if not Booking.objects.filter(vehicle_id=vehicle_id, trip_status=self.TripStatus.ONGOING).exists():
                Vehicle.objects.filter(pk=vehicle_id, status=Vehicle.Status.ON_TRIP).update(status=Vehicle.Status.AVAILABLE)
            return result
