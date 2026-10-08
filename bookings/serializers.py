from decimal import Decimal

from django.db.models import Q
from rest_framework import serializers

from .models import Booking, Customer

PAYMENT_STATUS_LABELS = {"paid": "Paid", "partial": "Partially Paid", "pending": "Pending"}


class CustomerSerializer(serializers.ModelSerializer):
    total_bookings = serializers.IntegerField(read_only=True, default=0)
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, default=0)
    paid_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, default=0)
    pending_amount = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "email", "address", "company_name", "gst_number", "notes",
                  "total_bookings", "total_amount", "paid_amount", "pending_amount"]

    def get_pending_amount(self, obj):
        return getattr(obj, "pending_amount", Decimal("0"))


class BookingSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.__str__", read_only=True)
    customer_phone = serializers.CharField(source="customer.phone", read_only=True)
    vehicle_label = serializers.CharField(source="vehicle.label", read_only=True)
    vehicle_type_name = serializers.CharField(source="vehicle.vehicle_type.name", read_only=True)
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    received = serializers.SerializerMethodField()
    later_payments = serializers.SerializerMethodField()
    balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    payment_status = serializers.CharField(read_only=True)
    total_km = serializers.IntegerField(read_only=True)
    duration_text = serializers.CharField(read_only=True)
    driver_stage = serializers.CharField(read_only=True)
    driver_stage_label = serializers.CharField(read_only=True)
    payment_status_label = serializers.SerializerMethodField()
    trip_status_label = serializers.CharField(source="get_trip_status_display", read_only=True)

    class Meta:
        model = Booking
        fields = ["id", "booking_number", "booking_date", "customer", "customer_name", "customer_phone",
                  "vehicle", "vehicle_label", "vehicle_type_name", "driver", "driver_name",
                  "trip_date", "return_date", "pickup_location", "destination", "number_of_days",
                  "booking_amount", "advance", "advance_method", "received", "later_payments", "balance",
                  "payment_status", "payment_status_label", "trip_status", "trip_status_label", "notes",
                  "accepted_at", "started_at", "start_odometer", "start_location", "completed_at", "end_odometer",
                  "end_location", "trip_notes", "total_km", "driver_stage", "driver_stage_label",
                  "duration_text"]
        # Filled in by the driver from the driver app.
        read_only_fields = ["booking_number", "accepted_at", "started_at", "start_odometer", "start_location",
                            "completed_at", "end_odometer", "end_location", "trip_notes"]

    def get_received(self, obj):
        return obj.get_received()

    def get_later_payments(self, obj):
        """Payments received after the advance (the advance itself is synced to its own income entry)."""
        return max(obj.get_received() - (obj.advance or 0), Decimal("0"))

    def get_payment_status_label(self, obj):
        return PAYMENT_STATUS_LABELS[obj.payment_status]

    def validate(self, attrs):
        get = lambda k: attrs.get(k, getattr(self.instance, k, None))  # noqa: E731
        trip_date, return_date = get("trip_date"), get("return_date")
        amount, advance = get("booking_amount") or 0, get("advance") or 0
        if return_date and trip_date and return_date < trip_date:
            raise serializers.ValidationError({"return_date": "Return date can't be before the trip date."})
        if advance < 0 or amount < 0:
            raise serializers.ValidationError({"advance": "Amounts can't be negative."})
        if advance > amount:
            raise serializers.ValidationError({"advance": "Advance can't be more than the booking amount."})

        # Don't allow the same vehicle on two overlapping trips.
        vehicle, status = get("vehicle"), get("trip_status")
        if vehicle and trip_date and status != Booking.TripStatus.CANCELLED:
            end = return_date or trip_date
            clash = (
                Booking.objects.filter(vehicle=vehicle, trip_date__lte=end)
                .filter(Q(return_date__gte=trip_date) | Q(return_date__isnull=True, trip_date__gte=trip_date))
                .exclude(trip_status__in=[Booking.TripStatus.CANCELLED, Booking.TripStatus.COMPLETED])
            )
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            other = clash.select_related("customer").first()
            if other:
                raise serializers.ValidationError({
                    "vehicle": f"{vehicle.label} is already booked for {other.customer.name} "
                               f"({other.booking_number}, {other.trip_date:%d %b}) on these dates."
                })
        return attrs
