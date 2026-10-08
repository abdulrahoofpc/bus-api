from rest_framework import serializers

from .models import DriverPayment, EMIInstallment, Expense, FuelEntry, Income, Loan, Maintenance

STATUS_LABELS = {"paid": "Paid", "overdue": "Overdue", "due_soon": "Due this week", "upcoming": "Upcoming"}


def positive(value):
    if value is None or value <= 0:
        raise serializers.ValidationError("Enter an amount greater than zero.")
    return value


class IncomeSerializer(serializers.ModelSerializer):
    vehicle_label = serializers.CharField(source="vehicle.label", read_only=True, default="")
    customer_name = serializers.CharField(source="customer.name", read_only=True, default="")
    booking_number = serializers.CharField(source="booking.booking_number", read_only=True, default="")
    income_type_label = serializers.CharField(source="get_income_type_display", read_only=True)
    payment_method_label = serializers.CharField(source="get_payment_method_display", read_only=True)

    class Meta:
        model = Income
        fields = ["id", "date", "vehicle", "vehicle_label", "booking", "booking_number", "customer",
                  "customer_name", "income_type", "income_type_label", "amount", "payment_method",
                  "payment_method_label", "reference", "notes", "is_booking_advance"]
        read_only_fields = ["is_booking_advance"]

    validate_amount = staticmethod(positive)


class ExpenseSerializer(serializers.ModelSerializer):
    vehicle_label = serializers.CharField(source="vehicle.label", read_only=True, default="")
    booking_number = serializers.CharField(source="booking.booking_number", read_only=True, default="")
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    payment_method_label = serializers.CharField(source="get_payment_method_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    is_linked = serializers.BooleanField(read_only=True)

    class Meta:
        model = Expense
        fields = ["id", "date", "vehicle", "vehicle_label", "booking", "booking_number", "category",
                  "category_label", "amount", "payment_method", "payment_method_label", "description",
                  "receipt", "source", "source_label", "is_linked", "status", "driver", "notes", "rejection_reason"]
        read_only_fields = ["source", "status", "driver", "rejection_reason"]

    validate_amount = staticmethod(positive)


class FuelEntrySerializer(serializers.ModelSerializer):
    vehicle_label = serializers.CharField(source="vehicle.label", read_only=True)
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    booking_number = serializers.CharField(source="booking.booking_number", read_only=True, default="")
    fuel_type_label = serializers.CharField(source="get_fuel_type_display", read_only=True)

    class Meta:
        model = FuelEntry
        fields = ["id", "date", "vehicle", "vehicle_label", "driver", "driver_name", "booking", "booking_number",
                  "fuel_type", "fuel_type_label", "litres", "rate", "total_amount", "odometer",
                  "fuel_station", "payment_method", "notes"]
        read_only_fields = ["total_amount"]

    validate_litres = staticmethod(positive)
    validate_rate = staticmethod(positive)


class MaintenanceSerializer(serializers.ModelSerializer):
    vehicle_label = serializers.CharField(source="vehicle.label", read_only=True)
    maintenance_type_label = serializers.CharField(source="get_maintenance_type_display", read_only=True)

    class Meta:
        model = Maintenance
        fields = ["id", "vehicle", "vehicle_label", "date", "maintenance_type", "maintenance_type_label",
                  "workshop", "description", "parts_cost", "labour_cost", "total_cost", "odometer",
                  "next_service_date", "payment_method", "notes", "status", "priority", "problem_category",
                  "photo", "driver"]
        read_only_fields = ["total_cost", "driver"]

    def validate(self, attrs):
        parts = attrs.get("parts_cost", getattr(self.instance, "parts_cost", 0)) or 0
        labour = attrs.get("labour_cost", getattr(self.instance, "labour_cost", 0)) or 0
        if parts < 0 or labour < 0:
            raise serializers.ValidationError({"parts_cost": "Costs can't be negative."})
        if parts + labour <= 0:
            raise serializers.ValidationError({"parts_cost": "Enter the parts cost, labour cost, or both."})
        return attrs


class DriverPaymentSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source="driver.name", read_only=True)
    payment_type_label = serializers.CharField(source="get_payment_type_display", read_only=True)
    payment_method_label = serializers.CharField(source="get_payment_method_display", read_only=True)

    class Meta:
        model = DriverPayment
        fields = ["id", "driver", "driver_name", "date", "payment_type", "payment_type_label", "salary_month",
                  "amount", "payment_method", "payment_method_label", "notes"]

    validate_amount = staticmethod(positive)


class InstallmentSerializer(serializers.ModelSerializer):
    status = serializers.CharField(read_only=True)
    status_label = serializers.SerializerMethodField()
    lender = serializers.CharField(source="loan.lender", read_only=True)
    vehicle = serializers.IntegerField(source="loan.vehicle_id", read_only=True)
    vehicle_label = serializers.CharField(source="loan.vehicle.label", read_only=True)
    tenure_months = serializers.IntegerField(source="loan.tenure_months", read_only=True)

    class Meta:
        model = EMIInstallment
        fields = ["id", "loan", "lender", "vehicle", "vehicle_label", "number", "tenure_months", "due_date",
                  "amount", "principal", "interest", "opening_balance", "closing_balance", "paid_date",
                  "paid_amount", "payment_method", "reference", "status", "status_label"]
        read_only_fields = fields

    def get_status_label(self, obj):
        return STATUS_LABELS[obj.status]


class LoanSerializer(serializers.ModelSerializer):
    vehicle_label = serializers.CharField(source="vehicle.label", read_only=True)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    remaining_emi_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    counts = serializers.SerializerMethodField()
    next_due = serializers.SerializerMethodField()

    class Meta:
        model = Loan
        fields = ["id", "vehicle", "vehicle_label", "lender", "account_number", "loan_amount", "down_payment",
                  "interest_rate", "start_date", "tenure_months", "emi_amount", "first_emi_date", "notes",
                  "is_closed", "outstanding", "remaining_emi_total", "counts", "next_due"]
        read_only_fields = ["is_closed"]
        extra_kwargs = {"emi_amount": {"required": False, "allow_null": True}}

    def get_counts(self, obj):
        counts = {"paid": 0, "overdue": 0, "due_soon": 0, "upcoming": 0}
        for inst in obj.installments.all():
            counts[inst.status] += 1
        return counts

    def get_next_due(self, obj):
        nxt = next((i for i in obj.installments.all() if not i.paid_date), None)
        return InstallmentSerializer(nxt).data if nxt else None

    def validate(self, attrs):
        if attrs.get("loan_amount") is not None and attrs["loan_amount"] <= 0:
            raise serializers.ValidationError({"loan_amount": "Enter the loan amount."})
        if attrs.get("tenure_months") is not None and attrs["tenure_months"] < 1:
            raise serializers.ValidationError({"tenure_months": "Tenure must be at least 1 month."})
        if self.instance and self.instance.installments.filter(paid_date__isnull=False).exists():
            changed = [f for f in Loan.TERM_FIELDS if f in attrs and attrs[f] != getattr(self.instance, f)]
            if changed:
                raise serializers.ValidationError(
                    "EMIs have already been paid on this loan, so the amount, rate, tenure and EMI dates "
                    "can't be changed. You can still edit the lender, account number and notes.")
        return attrs


class PayInstallmentSerializer(serializers.Serializer):
    paid_date = serializers.DateField(required=False)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    payment_method = serializers.CharField(required=False, allow_blank=True)
    reference = serializers.CharField(required=False, allow_blank=True)
