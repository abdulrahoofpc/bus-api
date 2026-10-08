"""
Validation for the driver API (/api/driver/). Every choice list (vehicles, trips) is limited to what
belongs to the logged-in driver, and is checked again on submit — so a driver
cannot post another driver's trip or vehicle ID.
"""
from decimal import Decimal

from django import forms
from django.conf import settings
from django.utils import timezone

from bookings.models import Booking
from common.choices import FuelType, PaymentMethod
from finance.models import Expense, Maintenance
from fleet.models import Vehicle

DRIVER_EXPENSE_CATEGORIES = [
    ("toll", "Toll"), ("parking", "Parking"), ("fuel", "Fuel"), ("food_allowance", "Food Allowance"),
    ("driver_allowance", "Driver Allowance"), ("road_expense", "Road Expense"), ("repair", "Repair"),
    ("spare_parts", "Spare Parts"), ("cleaning", "Cleaning"), ("permit", "Permit"), ("other", "Other"),
]
PROBLEM_TO_KIND = {"tyre": "tyres", "battery": "battery", "oil": "oil", "body": "body"}


def validate_upload(f):
    """Allowed file types and size for receipts, photos, videos and trip documents."""
    if not f:
        return f
    ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
    if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        raise forms.ValidationError("Please upload a photo (JPG, PNG, WEBP, HEIC), a PDF or a short video (MP4, MOV).")
    if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise forms.ValidationError(f"That file is too large. The limit is {settings.MAX_UPLOAD_MB} MB.")
    return f


class UploadField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", forms.ClearableFileInput(attrs={"accept": "image/*,application/pdf,video/*"}))
        super().__init__(*args, **kwargs)

    def clean(self, *args, **kwargs):
        return validate_upload(super().clean(*args, **kwargs))


def local_now():
    return timezone.localtime().replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")


class DateTimeLocal(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%dT%H:%M", **kwargs)


class DriverFormMixin:
    """Limits vehicle and trip choices to the driver's own."""

    def __init__(self, *args, driver=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.driver = driver
        trips = Booking.objects.filter(driver=driver).exclude(trip_status="cancelled").select_related("customer")
        self.my_trips = trips
        vehicle_ids = set(trips.values_list("vehicle_id", flat=True))
        if driver.assigned_vehicle_id:
            vehicle_ids.add(driver.assigned_vehicle_id)
        self.my_vehicles = Vehicle.objects.filter(pk__in=vehicle_ids)
        if "vehicle" in self.fields:
            self.fields["vehicle"].queryset = self.my_vehicles
            self.fields["vehicle"].initial = driver.assigned_vehicle_id
            self.fields["vehicle"].label_from_instance = lambda v: v.label
        if "booking" in self.fields:
            recent = trips.order_by("-trip_date")[:30]
            self.fields["booking"].queryset = trips
            self.fields["booking"].choices = [("", "No trip / not for a trip")] + [
                (b.pk, f"{b.booking_number} · {b.destination or b.customer.name} · {b.trip_date:%d %b}") for b in recent]

    def clean(self):
        data = super().clean()
        b, v = data.get("booking"), data.get("vehicle")
        if b and v and b.vehicle_id != v.pk:
            self.add_error("booking", "That trip was on a different vehicle.")
        return data


class FuelForm(DriverFormMixin, forms.Form):
    vehicle = forms.ModelChoiceField(queryset=Vehicle.objects.none(), empty_label=None)
    booking = forms.ModelChoiceField(queryset=Booking.objects.none(), required=False, label="Trip")
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), initial=lambda: timezone.localdate())
    fuel_type = forms.ChoiceField(choices=FuelType.choices)
    litres = forms.DecimalField(min_value=Decimal("0.1"), max_digits=10, decimal_places=2,
                                widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))
    rate = forms.DecimalField(label="Rate per litre", min_value=Decimal("1"), max_digits=10, decimal_places=2,
                              widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))
    odometer = forms.IntegerField(required=False, min_value=0, widget=forms.NumberInput(attrs={"inputmode": "numeric"}))
    fuel_station = forms.CharField(required=False, max_length=120)
    payment_method = forms.ChoiceField(choices=PaymentMethod.choices, initial="cash")
    receipt = UploadField(label="Receipt photo")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.driver.assigned_vehicle:
            self.fields["fuel_type"].initial = self.driver.assigned_vehicle.fuel_type


class ExpenseForm(DriverFormMixin, forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), initial=lambda: timezone.localdate())
    vehicle = forms.ModelChoiceField(queryset=Vehicle.objects.none(), empty_label=None)
    booking = forms.ModelChoiceField(queryset=Booking.objects.none(), required=False, label="Trip")
    category = forms.ChoiceField(choices=DRIVER_EXPENSE_CATEGORIES)
    amount = forms.DecimalField(min_value=Decimal("1"), max_digits=12, decimal_places=2,
                                widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))
    description = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={"placeholder": "What was it for?"}))
    payment_method = forms.ChoiceField(choices=PaymentMethod.choices, initial="cash")
    receipt = UploadField(label="Receipt photo")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class ProblemForm(DriverFormMixin, forms.Form):
    vehicle = forms.ModelChoiceField(queryset=Vehicle.objects.none(), empty_label=None)
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), initial=lambda: timezone.localdate())
    problem_category = forms.ChoiceField(label="Problem", choices=[c for c in Maintenance._meta.get_field("problem_category").choices if c[0]])
    priority = forms.ChoiceField(choices=Maintenance._meta.get_field("priority").choices, initial="medium")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "placeholder": "What is wrong? When did it start?"}))
    odometer = forms.IntegerField(label="Current odometer (km)", required=False, min_value=0,
                                  widget=forms.NumberInput(attrs={"inputmode": "numeric"}))
    photo = UploadField(label="Photo or short video")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class StartTripForm(forms.Form):
    start_odometer = forms.IntegerField(label="Starting odometer (km)", min_value=0,
                                        widget=forms.NumberInput(attrs={"inputmode": "numeric"}))
    start_location = forms.CharField(label="Starting location", max_length=150)
    started_at = forms.DateTimeField(label="Start date & time", widget=DateTimeLocal(), initial=local_now,
                                     input_formats=["%Y-%m-%dT%H:%M"])


class CompleteTripForm(forms.Form):
    end_odometer = forms.IntegerField(label="Ending odometer (km)", min_value=0,
                                      widget=forms.NumberInput(attrs={"inputmode": "numeric"}))
    end_location = forms.CharField(label="Ending location", max_length=150)
    completed_at = forms.DateTimeField(label="End date & time", widget=DateTimeLocal(), initial=local_now,
                                       input_formats=["%Y-%m-%dT%H:%M"])
    trip_notes = forms.CharField(label="Trip notes", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    extra_category = forms.ChoiceField(label="Additional expense (optional)", required=False,
                                       choices=[("", "No additional expense")] + DRIVER_EXPENSE_CATEGORIES)
    extra_amount = forms.DecimalField(label="Amount", required=False, min_value=Decimal("1"), max_digits=12,
                                      decimal_places=2, widget=forms.NumberInput(attrs={"inputmode": "decimal"}))
    extra_receipt = UploadField(label="Receipt for the additional expense")
    trip_document = UploadField(label="Trip document or photo")

    def __init__(self, *args, booking=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.booking = booking

    def clean(self):
        data = super().clean()
        start = self.booking.start_odometer
        end = data.get("end_odometer")
        if start is not None and end is not None and end < start:
            self.add_error("end_odometer", f"Must be at least the starting reading ({start} km).")
        if self.booking.started_at and data.get("completed_at") and data["completed_at"] < self.booking.started_at:
            self.add_error("completed_at", "The end time can't be before the start time.")
        if data.get("extra_category") and not data.get("extra_amount"):
            self.add_error("extra_amount", "Enter the amount for the additional expense.")
        return data


class UpdateTripForm(forms.Form):
    trip_notes = forms.CharField(label="Trip notes", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    trip_document = UploadField(label="Add a trip document or photo")


class PasswordForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    new_password = forms.CharField(min_length=8, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                                   help_text="At least 8 characters.")
    confirm = forms.CharField(label="New password again", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        data = super().clean()
        if data.get("current_password") and not self.user.check_password(data["current_password"]):
            self.add_error("current_password", "That isn't your current password.")
        if data.get("new_password") and data.get("new_password") != data.get("confirm"):
            self.add_error("confirm", "The two new passwords don't match.")
        if data.get("new_password"):
            from django.contrib.auth.password_validation import validate_password
            try:
                validate_password(data["new_password"], self.user)
            except forms.ValidationError as e:
                self.add_error("new_password", e)
        return data


# Map a problem category to the closest maintenance work type for the admin side.
def maintenance_kind(problem):
    return PROBLEM_TO_KIND.get(problem, "repair")


__all__ = ["FuelForm", "ExpenseForm", "ProblemForm", "StartTripForm", "CompleteTripForm", "UpdateTripForm",
           "PasswordForm", "maintenance_kind", "Expense"]


# ------------------------------------------------------------------ profile
import re as _re  # noqa: E402

from fleet.models import Driver  # noqa: E402

PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic"}


def _clean_phone(value, label="Phone number"):
    value = (value or "").strip()
    if value and not _re.fullmatch(r"\+?[\d\s-]{10,16}", value):
        raise forms.ValidationError(f"{label} should be 10 digits, e.g. 98765 43210 (a +91 prefix is fine).")
    return value


class ProfileForm(forms.ModelForm):
    """What a driver may change about themselves. Name, salary, status and vehicle stay with the office."""

    class Meta:
        model = Driver
        fields = ["photo", "phone", "email", "address", "blood_group", "emergency_contact_name",
                  "emergency_contact_phone", "licence_number", "licence_expiry"]
        labels = {"phone": "Mobile number", "emergency_contact_name": "Name", "emergency_contact_phone": "Phone",
                  "licence_number": "Licence number", "licence_expiry": "Licence valid until"}
        widgets = {
            "photo": forms.ClearableFileInput(attrs={"accept": "image/*", "capture": "user"}),
            "phone": forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "address": forms.Textarea(attrs={"rows": 3, "autocomplete": "street-address"}),
            "emergency_contact_phone": forms.TextInput(attrs={"inputmode": "tel"}),
            "licence_expiry": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["blood_group"].choices = [("", "Not sure")] + [c for c in self.fields["blood_group"].choices if c[0]]

    def clean_photo(self):
        f = self.cleaned_data.get("photo")
        if f and hasattr(f, "size") and getattr(f, "content_type", None) is not None:
            ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
            if ext not in PHOTO_EXTENSIONS:
                raise forms.ValidationError("Please choose a photo (JPG, PNG, WEBP or HEIC).")
            if f.size > 5 * 1024 * 1024:
                raise forms.ValidationError("That photo is too large. The limit is 5 MB.")
        return f

    def clean_phone(self):
        value = _clean_phone(self.cleaned_data.get("phone"))
        if not value:
            raise forms.ValidationError("The office needs a mobile number to reach you.")
        return value

    def clean_emergency_contact_phone(self):
        return _clean_phone(self.cleaned_data.get("emergency_contact_phone"), "Emergency phone")

    def clean(self):
        data = super().clean()
        if data.get("emergency_contact_phone") and not data.get("emergency_contact_name"):
            self.add_error("emergency_contact_name", "Whose number is this?")
        return data
