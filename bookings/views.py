from decimal import Decimal

import django_filters
from django.db.models import Count, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework.decorators import action
from rest_framework.response import Response

from common.filters import PeriodVehicleFilter
from common.views import BaseViewSet

from .models import MONEY, Booking, Customer, received_subquery
from .serializers import BookingSerializer, CustomerSerializer

ZERO = Value(Decimal("0"), output_field=MONEY)


def customer_queryset():
    active = Booking.objects.filter(customer=OuterRef("pk")).exclude(trip_status="cancelled")
    total = Subquery(active.values("customer").annotate(t=Sum("booking_amount")).values("t")[:1], output_field=MONEY)
    received = Subquery(
        active.annotate(r=received_subquery()).values("customer").annotate(t=Sum("r")).values("t")[:1],
        output_field=MONEY,
    )
    return Customer.objects.annotate(
        total_bookings=Count("bookings", filter=~Q(bookings__trip_status="cancelled"), distinct=True),
        total_amount=Coalesce(total, ZERO),
        paid_amount=Coalesce(received, ZERO),
    ).annotate(
        pending_amount=ExpressionWrapper(F("total_amount") - F("paid_amount"), output_field=MONEY)
    ).order_by("name")


class CustomerFilter(django_filters.FilterSet):
    has_pending = django_filters.BooleanFilter(method="filter_pending")

    class Meta:
        model = Customer
        fields = ["has_pending"]

    def filter_pending(self, queryset, name, value):
        return queryset.filter(pending_amount__gt=0) if value else queryset


class CustomerViewSet(BaseViewSet):
    module = "customers"
    serializer_class = CustomerSerializer
    filterset_class = CustomerFilter
    search_fields = ["name", "phone", "company_name", "email", "gst_number"]
    ordering_fields = ["name", "pending_amount", "total_amount"]
    protected_message = "This customer has bookings or payments, so they can't be deleted."

    def get_queryset(self):
        return customer_queryset()


class BookingFilter(PeriodVehicleFilter):
    date_field = "trip_date"
    payment_status = django_filters.CharFilter(method="filter_payment_status")

    class Meta:
        model = Booking
        fields = ["vehicle", "customer", "driver", "trip_status"]

    def filter_payment_status(self, queryset, name, value):
        if value == "paid":
            return queryset.filter(booking_amount__gt=0, received__gte=F("booking_amount"))
        if value == "partial":
            return queryset.filter(received__gt=0, received__lt=F("booking_amount"))
        if value == "pending":
            return queryset.filter(received__lte=0)
        if value == "due":  # anything still owed
            return queryset.exclude(trip_status="cancelled").filter(received__lt=F("booking_amount"))
        return queryset


class BookingViewSet(BaseViewSet):
    module = "bookings"
    serializer_class = BookingSerializer
    filterset_class = BookingFilter
    sum_fields = ("booking_amount", "received")
    search_fields = ["booking_number", "customer__name", "customer__phone", "vehicle__registration_number",
                     "vehicle__short_name", "destination", "pickup_location"]
    ordering_fields = ["trip_date", "booking_amount", "booking_number"]
    protected_message = ("Payments have been recorded against this booking. "
                         "Delete those income entries first, or mark the booking Cancelled.")

    def get_queryset(self):
        return Booking.objects.with_totals().select_related(
            "customer", "vehicle", "vehicle__vehicle_type", "driver")

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        from reports.services import trip_summary

        return Response(trip_summary(self.get_object(), request.user))
