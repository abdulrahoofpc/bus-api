from datetime import timedelta

import django_filters
from django.db.models import Count, Q
from rest_framework.decorators import action
from rest_framework.response import Response

from common.dates import today
from common.views import BaseViewSet

from .models import Driver, Vehicle, VehicleDocument, VehicleType
from .serializers import (DriverSerializer, VehicleDocumentSerializer,
                          VehicleSerializer, VehicleTypeSerializer)


class VehicleTypeViewSet(BaseViewSet):
    module = "vehicle_types"
    serializer_class = VehicleTypeSerializer
    protected_message = "Vehicles of this type exist. Move them to another type first, or mark this type inactive."
    queryset = VehicleType.objects.annotate(vehicle_count=Count("vehicles")).order_by("sort_order", "name")
    search_fields = ["name"]


class VehicleViewSet(BaseViewSet):
    module = "vehicles"
    serializer_class = VehicleSerializer
    queryset = Vehicle.objects.select_related("vehicle_type")
    filterset_fields = ["vehicle_type", "status", "fuel_type"]
    search_fields = ["registration_number", "short_name", "brand", "model"]
    ordering_fields = ["registration_number", "short_name", "year", "status"]
    protected_message = ("This vehicle has trips or accounts linked to it, so it can't be deleted. "
                         "Set its status to Sold or Inactive instead.")

    @action(detail=True, methods=["get"])
    def profile(self, request, pk=None):
        from reports.services import vehicle_profile

        return Response(vehicle_profile(self.get_object(), request.user, request.query_params))


class DocumentFilter(django_filters.FilterSet):
    vehicle_type = django_filters.NumberFilter(field_name="vehicle__vehicle_type")
    status = django_filters.CharFilter(method="filter_status")

    class Meta:
        model = VehicleDocument
        fields = ["vehicle", "doc_type", "vehicle_type", "status"]

    def filter_status(self, queryset, name, value):
        t = today()
        if value == "expired":
            return queryset.filter(expiry_date__lt=t)
        if value == "7_days":
            return queryset.filter(expiry_date__gte=t, expiry_date__lte=t + timedelta(days=7))
        if value == "30_days":
            return queryset.filter(expiry_date__gt=t + timedelta(days=7), expiry_date__lte=t + timedelta(days=30))
        if value == "attention":
            return queryset.filter(expiry_date__lte=t + timedelta(days=30))
        if value == "valid":
            return queryset.filter(expiry_date__gt=t + timedelta(days=30))
        return queryset


class VehicleDocumentViewSet(BaseViewSet):
    module = "documents"
    serializer_class = VehicleDocumentSerializer
    queryset = VehicleDocument.objects.select_related("vehicle", "vehicle__vehicle_type")
    filterset_class = DocumentFilter
    search_fields = ["vehicle__registration_number", "vehicle__short_name", "document_number", "provider"]
    ordering_fields = ["expiry_date", "doc_type"]


class DriverViewSet(BaseViewSet):
    module = "drivers"
    serializer_class = DriverSerializer
    queryset = Driver.objects.select_related("assigned_vehicle").annotate(
        trip_count=Count("bookings", filter=~Q(bookings__trip_status="cancelled"))
    ).order_by("name")
    filterset_fields = ["status", "assigned_vehicle"]
    search_fields = ["name", "phone", "licence_number"]
    protected_message = "This driver has salary payments recorded. Set status to Inactive instead."

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        from reports.services import driver_summary

        return Response(driver_summary(self.get_object(), request.user, request.query_params))
