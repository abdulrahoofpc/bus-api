import django_filters
from django.db.models import Prefetch, Sum
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from common.dates import today
from common.filters import PeriodVehicleFilter
from common.views import BaseViewSet

from .models import DriverPayment, EMIInstallment, Expense, FuelEntry, Income, Loan, Maintenance
from .serializers import (DriverPaymentSerializer, ExpenseSerializer, FuelEntrySerializer,
                          IncomeSerializer, InstallmentSerializer, LoanSerializer,
                          MaintenanceSerializer, PayInstallmentSerializer)


# ---- Income -----------------------------------------------------------------
class IncomeFilter(PeriodVehicleFilter):
    class Meta:
        model = Income
        fields = ["vehicle", "booking", "customer", "income_type", "payment_method"]


class IncomeViewSet(BaseViewSet):
    module = "income"
    serializer_class = IncomeSerializer
    filterset_class = IncomeFilter
    sum_fields = ("amount",)
    search_fields = ["reference", "notes", "customer__name", "vehicle__registration_number",
                     "vehicle__short_name", "booking__booking_number"]
    ordering_fields = ["date", "amount"]
    queryset = Income.objects.select_related("vehicle", "customer", "booking")

    LOCKED = "This advance comes from booking {}. Edit the booking to change it."

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.is_booking_advance:
            return Response({"detail": self.LOCKED.format(obj.booking.booking_number)}, status=400)
        return super().update(request, *args, **kwargs)

    def check_can_delete(self, instance):
        if instance.is_booking_advance:
            return self.LOCKED.format(instance.booking.booking_number)
        return None


# ---- Expenses ---------------------------------------------------------------
class ExpenseFilter(PeriodVehicleFilter):
    exclude_emi = django_filters.BooleanFilter(method="filter_exclude_emi")

    class Meta:
        model = Expense
        fields = ["vehicle", "booking", "category", "payment_method", "source"]

    def filter_exclude_emi(self, queryset, name, value):
        return queryset.exclude(category=Expense.Category.EMI) if value else queryset


LINKED_HINT = {
    "fuel": "Fuel", "maintenance": "Maintenance", "driver": "Drivers (salary payments)", "emi": "Finance / EMI",
}


class ExpenseViewSet(BaseViewSet):
    module = "expenses"
    serializer_class = ExpenseSerializer
    filterset_class = ExpenseFilter
    sum_fields = ("amount",)
    search_fields = ["description", "vehicle__registration_number", "vehicle__short_name"]
    ordering_fields = ["date", "amount", "category"]
    queryset = Expense.objects.select_related("vehicle", "booking")

    def get_queryset(self):
        # Default: approved only (as always). ?status=pending|rejected|all shows driver submissions too.
        params = getattr(self.request, "query_params", None) or getattr(self.request, "GET", {})
        st = params.get("status")
        if st in ("pending", "rejected", "approved", "all") or getattr(self, "action", "list") not in ("list", None):
            qs = Expense.all_objects.select_related("vehicle", "booking", "driver")
            return qs.filter(status=st) if st in ("pending", "rejected", "approved") else qs
        return super().get_queryset()

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        # money totals never include pending or rejected submissions
        qs = self.filter_queryset(self.get_queryset()).filter(status="approved")
        response.data["totals"] = {"amount": qs.aggregate(t=Sum("amount"))["t"] or 0}
        return response

    def _locked(self, obj):
        return f"This expense was created from {LINKED_HINT[obj.source]}. Change it there."

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.status == "rejected":
            return Response({"detail": "Rejected expenses can't be changed. They don't count in any totals."}, status=400)
        if obj.is_linked:
            return Response({"detail": self._locked(obj)}, status=400)
        return super().update(request, *args, **kwargs)

    def check_can_delete(self, instance):
        return self._locked(instance) if instance.is_linked else None


# ---- Fuel -------------------------------------------------------------------
class FuelFilter(PeriodVehicleFilter):
    class Meta:
        model = FuelEntry
        fields = ["vehicle", "driver", "booking", "fuel_type"]


class FuelEntryViewSet(BaseViewSet):
    module = "fuel"
    serializer_class = FuelEntrySerializer
    filterset_class = FuelFilter
    sum_fields = ("total_amount", "litres")
    search_fields = ["fuel_station", "vehicle__registration_number", "vehicle__short_name", "driver__name"]
    ordering_fields = ["date", "total_amount", "litres"]
    queryset = FuelEntry.objects.select_related("vehicle", "driver", "booking")


# ---- Maintenance ------------------------------------------------------------
class MaintenanceFilter(PeriodVehicleFilter):
    class Meta:
        model = Maintenance
        fields = ["vehicle", "maintenance_type"]


class MaintenanceViewSet(BaseViewSet):
    module = "maintenance"
    serializer_class = MaintenanceSerializer
    filterset_class = MaintenanceFilter
    sum_fields = ("total_cost",)
    search_fields = ["workshop", "description", "vehicle__registration_number", "vehicle__short_name"]
    ordering_fields = ["date", "total_cost", "next_service_date"]
    queryset = Maintenance.objects.select_related("vehicle", "driver")

    def get_queryset(self):
        qs = super().get_queryset()
        params = getattr(self.request, "query_params", None) or getattr(self.request, "GET", {})
        st = params.get("status")
        if st == "open":
            return qs.filter(status__in=["reported", "under_review", "approved", "in_progress"])
        return qs.filter(status=st) if st else qs

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        # only finished work is a cost
        qs = self.filter_queryset(self.get_queryset()).filter(status="completed")
        response.data["totals"] = {"total_cost": qs.aggregate(t=Sum("total_cost"))["t"] or 0}
        return response


# ---- Driver payments ----------------------------------------------------------
class DriverPaymentFilter(PeriodVehicleFilter):
    vehicle_field = "driver__assigned_vehicle"

    class Meta:
        model = DriverPayment
        fields = ["driver", "payment_type"]


class DriverPaymentViewSet(BaseViewSet):
    module = "driver_payments"
    serializer_class = DriverPaymentSerializer
    filterset_class = DriverPaymentFilter
    sum_fields = ("amount",)
    search_fields = ["driver__name", "notes"]
    queryset = DriverPayment.objects.select_related("driver")


# ---- Loans & EMI --------------------------------------------------------------
class LoanViewSet(BaseViewSet):
    module = "finance"
    serializer_class = LoanSerializer
    filterset_fields = ["vehicle", "is_closed", "vehicle__vehicle_type"]
    search_fields = ["lender", "account_number", "vehicle__registration_number", "vehicle__short_name"]
    queryset = Loan.objects.select_related("vehicle").prefetch_related(
        Prefetch("installments", queryset=EMIInstallment.objects.order_by("number")))

    def check_can_delete(self, instance):
        if instance.installments.filter(paid_date__isnull=False).exists():
            return "EMIs have been paid on this loan. Undo those payments before deleting it."
        return None

    @action(detail=True, methods=["get"])
    def schedule(self, request, pk=None):
        loan = self.get_object()
        return Response(InstallmentSerializer(loan.installments.all(), many=True).data)


class InstallmentFilter(django_filters.FilterSet):
    status = django_filters.CharFilter(method="filter_status")
    vehicle = django_filters.NumberFilter(field_name="loan__vehicle")
    vehicle_type = django_filters.NumberFilter(field_name="loan__vehicle__vehicle_type")
    due_from = django_filters.DateFilter(field_name="due_date", lookup_expr="gte")
    due_to = django_filters.DateFilter(field_name="due_date", lookup_expr="lte")

    class Meta:
        model = EMIInstallment
        fields = ["loan", "status", "vehicle", "vehicle_type"]

    def filter_status(self, queryset, name, value):
        from datetime import timedelta
        t = today()
        if value == "paid":
            return queryset.filter(paid_date__isnull=False)
        unpaid = queryset.filter(paid_date__isnull=True)
        if value == "unpaid":
            return unpaid
        if value == "overdue":
            return unpaid.filter(due_date__lt=t)
        if value == "due_soon":
            return unpaid.filter(due_date__gte=t, due_date__lte=t + timedelta(days=7))
        if value == "upcoming":
            return unpaid.filter(due_date__gte=t)
        if value == "payable":  # overdue + next 30 days
            return unpaid.filter(due_date__lte=t + timedelta(days=30))
        return queryset


class InstallmentViewSet(BaseViewSet):
    module = "finance"
    serializer_class = InstallmentSerializer
    filterset_class = InstallmentFilter
    sum_fields = ("amount",)
    http_method_names = ["get", "post", "head", "options"]
    queryset = EMIInstallment.objects.select_related("loan", "loan__vehicle")
    ordering_fields = ["due_date"]

    def create(self, request, *args, **kwargs):
        return Response({"detail": "EMIs are created from the loan schedule."}, status=405)

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        inst = self.get_object()
        if inst.paid_date:
            return Response({"detail": "This EMI is already marked as paid."}, status=400)
        ser = PayInstallmentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        inst.mark_paid(d.get("paid_date"), d.get("amount"), d.get("payment_method"), d.get("reference"))
        inst.refresh_from_db()
        return Response(InstallmentSerializer(inst).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def undo(self, request, pk=None):
        inst = self.get_object()
        if not inst.paid_date:
            return Response({"detail": "This EMI isn't paid yet."}, status=400)
        inst.undo_payment()
        return Response(InstallmentSerializer(inst).data)
