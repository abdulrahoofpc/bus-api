"""
Money in and money out.

Every rupee that leaves the business ends up as an Expense row. Fuel entries,
maintenance jobs, driver payments and EMI payments create (and keep in sync)
their own linked Expense automatically, so reports never double count and the
user never has to enter the same amount twice.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models, transaction
from django.db.models import Sum

from bookings.models import Booking, Customer
from common.choices import FuelType, PaymentMethod
from common.dates import add_months, today
from fleet.models import Driver, Vehicle

CENT = Decimal("0.01")


def q(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class Income(models.Model):
    class IncomeType(models.TextChoices):
        TRIP = "trip", "Trip Income"
        RENTAL = "rental", "Vehicle Rental"
        ADVANCE = "advance", "Advance"
        OTHER = "other", "Other Income"

    date = models.DateField(default=today)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, null=True, blank=True, related_name="incomes")
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, null=True, blank=True, related_name="incomes")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, null=True, blank=True, related_name="incomes")
    income_type = models.CharField(max_length=20, choices=IncomeType.choices, default=IncomeType.TRIP)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    is_booking_advance = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def save(self, *args, **kwargs):
        # Fill vehicle/customer from the booking so vehicle-wise reports stay correct.
        if self.booking_id:
            self.vehicle_id = self.vehicle_id or self.booking.vehicle_id
            self.customer_id = self.customer_id or self.booking.customer_id
        super().save(*args, **kwargs)


class ApprovedExpenseManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(status="approved")


class Expense(models.Model):
    class Category(models.TextChoices):
        FUEL = "fuel", "Fuel"
        MAINTENANCE = "maintenance", "Maintenance"
        DRIVER_SALARY = "driver_salary", "Driver Salary"
        TOLL = "toll", "Toll"
        PARKING = "parking", "Parking"
        INSURANCE = "insurance", "Insurance"
        PERMIT = "permit", "Permit"
        TAX = "tax", "Tax"
        EMI = "emi", "EMI"
        SPARE_PARTS = "spare_parts", "Spare Parts"
        OFFICE = "office", "Office Expense"
        FOOD_ALLOWANCE = "food_allowance", "Food Allowance"
        DRIVER_ALLOWANCE = "driver_allowance", "Driver Allowance"
        ROAD_EXPENSE = "road_expense", "Road Expense"
        REPAIR = "repair", "Repair"
        CLEANING = "cleaning", "Cleaning"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    class Source(models.TextChoices):
        MANUAL = "manual", "Entered directly"
        FUEL = "fuel", "Fuel entry"
        MAINTENANCE = "maintenance", "Maintenance"
        DRIVER = "driver", "Driver payment"
        EMI = "emi", "EMI payment"

    date = models.DateField(default=today)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, null=True, blank=True, related_name="expenses")
    booking = models.ForeignKey(Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    category = models.CharField(max_length=20, choices=Category.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    description = models.CharField(max_length=255, blank=True)
    receipt = models.FileField(upload_to="receipts/%Y/%m/", null=True, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL, editable=False)
    fuel_entry = models.OneToOneField("FuelEntry", on_delete=models.CASCADE, null=True, blank=True,
                                      related_name="expense", editable=False)
    maintenance = models.OneToOneField("Maintenance", on_delete=models.CASCADE, null=True, blank=True,
                                       related_name="expense", editable=False)
    driver_payment = models.OneToOneField("DriverPayment", on_delete=models.CASCADE, null=True, blank=True,
                                          related_name="expense", editable=False)
    installment = models.OneToOneField("EMIInstallment", on_delete=models.CASCADE, null=True, blank=True,
                                       related_name="expense", editable=False)
    # Driver submissions start as Pending and count in finance only once Approved.
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.APPROVED)
    driver = models.ForeignKey("fleet.Driver", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="submitted_expenses")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="+")
    notes = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # `objects` holds only APPROVED expenses, so every total, report and profit figure ignores
    # pending and rejected driver submissions automatically. `all_objects` sees everything.
    objects = ApprovedExpenseManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-date", "-id"]
        base_manager_name = "all_objects"

    @property
    def is_linked(self):
        return self.source != self.Source.MANUAL


def sync_expense(link_field, obj, **values):
    """Create or update the Expense that mirrors a fuel/maintenance/driver/EMI record."""
    expense = Expense.all_objects.filter(**{link_field: obj}).first() or Expense(**{link_field: obj})
    for key, value in values.items():
        setattr(expense, key, value)
    expense.save()
    return expense


class FuelEntry(models.Model):
    date = models.DateField(default=today)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="fuel_entries")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name="fuel_entries")
    booking = models.ForeignKey(Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name="fuel_entries")
    fuel_type = models.CharField(max_length=20, choices=FuelType.choices, default=FuelType.DIESEL)
    litres = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField("Rate per litre", max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, editable=False)
    odometer = models.PositiveIntegerField(null=True, blank=True)
    fuel_station = models.CharField(max_length=120, blank=True)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    receipt = models.FileField(upload_to="fuel/%Y/%m/", null=True, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="+")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "fuel entries"

    def save(self, *args, **kwargs):
        self.total_amount = q(self.litres * self.rate)  # Total = Litres x Rate
        with transaction.atomic():
            super().save(*args, **kwargs)
            sync_expense(
                "fuel_entry", self, source=Expense.Source.FUEL, category=Expense.Category.FUEL,
                date=self.date, vehicle=self.vehicle, booking=self.booking, amount=self.total_amount,
                payment_method=self.payment_method,
                description=f"{self.litres} L {self.get_fuel_type_display()} @ {self.rate}"
                            + (f" - {self.fuel_station}" if self.fuel_station else ""),
            )


class Maintenance(models.Model):
    class Kind(models.TextChoices):
        SERVICE = "service", "General Service"
        REPAIR = "repair", "Repair"
        OIL = "oil", "Oil Change"
        TYRES = "tyres", "Tyres"
        BATTERY = "battery", "Battery"
        BODY = "body", "Body / Paint"
        BREAKDOWN = "breakdown", "Breakdown"
        OTHER = "other", "Other"

    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="maintenance_records")
    date = models.DateField(default=today)
    maintenance_type = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SERVICE)
    workshop = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    parts_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    labour_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, editable=False, default=0)
    odometer = models.PositiveIntegerField(null=True, blank=True)
    next_service_date = models.DateField(null=True, blank=True)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    notes = models.TextField(blank=True)
    # Driver problem reports: reported → under review → approved → in progress → completed (or rejected).
    status = models.CharField(max_length=20, choices=[
        ("reported", "Reported"), ("under_review", "Under Review"), ("approved", "Approved"),
        ("in_progress", "In Progress"), ("completed", "Completed"), ("rejected", "Rejected")], default="completed")
    priority = models.CharField(max_length=10, choices=[
        ("low", "Low"), ("medium", "Medium"), ("high", "High"), ("emergency", "Emergency")], default="medium")
    problem_category = models.CharField(max_length=20, blank=True, choices=[
        ("engine", "Engine"), ("tyre", "Tyre"), ("brake", "Brake"), ("battery", "Battery"), ("ac", "AC"),
        ("electrical", "Electrical"), ("body", "Body"), ("oil", "Oil"), ("suspension", "Suspension"), ("other", "Other")])
    driver = models.ForeignKey("fleet.Driver", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="maintenance_reports")
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    photo = models.FileField(upload_to="maintenance/%Y/%m/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ["-date", "-id"]

    @property
    def is_completed(self):
        return self.status == "completed"

    def save(self, *args, **kwargs):
        self.total_cost = q((self.parts_cost or 0) + (self.labour_cost or 0))
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.status != "completed":
                # Only finished work costs money: remove any expense while it is still open or was rejected.
                Expense.all_objects.filter(maintenance=self).delete()
                return
            sync_expense(
                "maintenance", self, source=Expense.Source.MAINTENANCE, category=Expense.Category.MAINTENANCE,
                date=self.date, vehicle=self.vehicle, amount=self.total_cost,
                payment_method=self.payment_method,
                description=f"{self.get_maintenance_type_display()}" + (f" at {self.workshop}" if self.workshop else ""),
            )


class DriverPayment(models.Model):
    class Kind(models.TextChoices):
        SALARY = "salary", "Salary"
        ADVANCE = "advance", "Salary Advance"
        ALLOWANCE = "allowance", "Allowance / Bata"

    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="payments")
    date = models.DateField(default=today)
    payment_type = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SALARY)
    salary_month = models.DateField(help_text="Any date in the month this payment belongs to.")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def save(self, *args, **kwargs):
        self.salary_month = (self.salary_month or self.date).replace(day=1)
        with transaction.atomic():
            super().save(*args, **kwargs)
            sync_expense(
                "driver_payment", self, source=Expense.Source.DRIVER, category=Expense.Category.DRIVER_SALARY,
                date=self.date, vehicle=self.driver.assigned_vehicle, amount=self.amount,
                payment_method=self.payment_method,
                description=f"{self.get_payment_type_display()} - {self.driver.name} ({self.salary_month:%b %Y})",
            )


class Loan(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="loans")
    lender = models.CharField("Finance company / bank", max_length=120)
    account_number = models.CharField("Loan account number", max_length=60, blank=True)
    loan_amount = models.DecimalField(max_digits=14, decimal_places=2)
    down_payment = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    interest_rate = models.DecimalField("Interest rate (% per year)", max_digits=6, decimal_places=2, default=0)
    start_date = models.DateField()
    tenure_months = models.PositiveSmallIntegerField()
    emi_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                     help_text="Leave empty to calculate automatically.")
    first_emi_date = models.DateField(help_text="Due date of the first EMI; later EMIs fall on the same day each month.")
    notes = models.TextField(blank=True)
    is_closed = models.BooleanField(default=False)

    TERM_FIELDS = ("loan_amount", "interest_rate", "tenure_months", "emi_amount", "first_emi_date")

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.lender} - {self.vehicle}"

    @staticmethod
    def calculate_emi(principal, annual_rate, months):
        principal, months = Decimal(principal), int(months)
        r = Decimal(annual_rate) / Decimal(1200)
        if months <= 0:
            return Decimal("0")
        if r == 0:
            return q(principal / months)
        factor = (1 + r) ** months
        return q(principal * r * factor / (factor - 1))

    def build_schedule(self):
        """Replace the EMI schedule (only allowed while nothing has been paid)."""
        self.installments.all().delete()
        r = Decimal(self.interest_rate) / Decimal(1200)
        emi = Decimal(self.emi_amount)
        balance = Decimal(self.loan_amount)
        rows = []
        for n in range(1, self.tenure_months + 1):
            interest = q(balance * r)
            principal = emi - interest
            if n == self.tenure_months or principal > balance:
                principal = balance
            amount = q(principal + interest)
            closing = q(balance - principal)
            rows.append(EMIInstallment(
                loan=self, number=n, due_date=add_months(self.first_emi_date, n - 1),
                amount=amount, principal=q(principal), interest=interest,
                opening_balance=q(balance), closing_balance=closing,
            ))
            balance = closing
            if balance <= 0:
                break
        EMIInstallment.objects.bulk_create(rows)

    def save(self, *args, **kwargs):
        rebuild = self._state.adding
        if not self.emi_amount:
            self.emi_amount = self.calculate_emi(self.loan_amount, self.interest_rate, self.tenure_months)
        if self.pk and not rebuild:
            old = Loan.objects.filter(pk=self.pk).values(*self.TERM_FIELDS).first()
            rebuild = any(old[f] != getattr(self, f) for f in self.TERM_FIELDS)
        with transaction.atomic():
            super().save(*args, **kwargs)
            if rebuild:
                self.build_schedule()

    # ---- live figures ------------------------------------------------------
    @property
    def principal_paid(self):
        return self.installments.filter(paid_date__isnull=False).aggregate(t=Sum("principal"))["t"] or Decimal("0")

    @property
    def outstanding(self):
        """Principal still owed. Updates automatically after each EMI payment."""
        return q(Decimal(self.loan_amount) - self.principal_paid)

    @property
    def remaining_emi_total(self):
        return self.installments.filter(paid_date__isnull=True).aggregate(t=Sum("amount"))["t"] or Decimal("0")


class EMIInstallment(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="installments")
    number = models.PositiveSmallIntegerField()
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    principal = models.DecimalField(max_digits=14, decimal_places=2)
    interest = models.DecimalField(max_digits=14, decimal_places=2)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2)
    closing_balance = models.DecimalField(max_digits=14, decimal_places=2)
    paid_date = models.DateField(null=True, blank=True)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, blank=True)
    reference = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["due_date", "number"]
        unique_together = [("loan", "number")]

    @property
    def status(self):
        if self.paid_date:
            return "paid"
        days = (self.due_date - today()).days
        if days < 0:
            return "overdue"
        if days <= 7:
            return "due_soon"
        return "upcoming"

    def mark_paid(self, paid_date=None, amount=None, method=PaymentMethod.BANK, reference=""):
        with transaction.atomic():
            self.paid_date = paid_date or today()
            self.paid_amount = Decimal(amount) if amount not in (None, "") else self.amount
            self.payment_method = method or PaymentMethod.BANK
            self.reference = reference or ""
            self.save()
            sync_expense(
                "installment", self, source=Expense.Source.EMI, category=Expense.Category.EMI,
                date=self.paid_date, vehicle=self.loan.vehicle, amount=self.paid_amount,
                payment_method=self.payment_method,
                description=f"EMI {self.number}/{self.loan.tenure_months} - {self.loan.lender}",
            )
            if not self.loan.installments.filter(paid_date__isnull=True).exists():
                Loan.objects.filter(pk=self.loan_id).update(is_closed=True)

    def undo_payment(self):
        with transaction.atomic():
            Expense.objects.filter(installment=self).delete()
            self.paid_date = None
            self.paid_amount = None
            self.payment_method = ""
            self.reference = ""
            self.save()
            Loan.objects.filter(pk=self.loan_id).update(is_closed=False)
