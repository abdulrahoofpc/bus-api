"""
Every office module (vehicles, bookings, income, …) is described here once:
its form fields, filters, totals and save rules. /api/office/resources/ sends
these definitions to the app, which builds its list and form screens from them;
/api/office/r/<key>/ lists, adds, edits and deletes the records.

Saving always goes through the same DRF serializers the API uses, so every
business rule (balances, advance → income, no overlapping bookings, locked
linked expenses, EMI terms locked after payment…) lives in one place.
"""

from accounts.models import User
from accounts.serializers import UserSerializer
from bookings.models import Booking, Customer
from bookings.views import BookingViewSet, CustomerViewSet
from finance.models import DriverPayment, Expense, FuelEntry, Income, Maintenance
from finance.views import (DriverPaymentViewSet, ExpenseViewSet, FuelEntryViewSet, IncomeViewSet,
                           LoanViewSet, MaintenanceViewSet)
from fleet.models import Driver, Vehicle, VehicleDocument, VehicleType
from fleet.views import DriverViewSet, VehicleDocumentViewSet, VehicleTypeViewSet, VehicleViewSet

from common.dates import today
from .formatting import number, rupees, short_date


# ---------------------------------------------------------------- choice helpers
def model_choices(model, field):
    return list(model._meta.get_field(field).choices)


def vehicle_choices(request=None, include_sold=False):
    qs = Vehicle.objects.select_related("vehicle_type")
    if not include_sold:
        qs = qs.exclude(status="sold")
    return [(v.id, f"{v.label} — {v.vehicle_type.name}") for v in qs]


def all_vehicle_choices(request=None):
    return vehicle_choices(request, include_sold=True)


def vehicle_type_choices(request=None, active_only=False):
    qs = VehicleType.objects.all()
    if active_only:
        qs = qs.filter(is_active=True)
    return [(t.id, t.name) for t in qs]


def active_vehicle_type_choices(request=None):
    return vehicle_type_choices(request, active_only=True)


def customer_choices(request=None):
    return [(c.id, f"{c.name}{' — ' + c.company_name if c.company_name else ''}") for c in Customer.objects.all()]


def driver_choices(request=None):
    return [(d.id, d.name) for d in Driver.objects.exclude(status="inactive")]


def booking_choices(request=None):
    rows = []
    for b in Booking.objects.with_totals().select_related("customer").order_by("-trip_date")[:400]:
        due = b.balance if b.trip_status != "cancelled" else 0
        hint = f"{rupees(due)} due" if due > 0 else short_date(b.trip_date)
        rows.append((b.id, f"{b.booking_number} · {b.customer.name} · {hint}"))
    return rows


# ---------------------------------------------------------------- form fields
def F(name, label, kind="text", required=False, half=True, choices=None, help="", placeholder="", step=None,
      min=None, show_when=None):
    """A form field. kind: text, textarea, number, money, date, select, file, checkbox, password."""
    return {"name": name, "label": label, "kind": kind, "required": required, "half": half, "choices": choices,
            "help": help, "placeholder": placeholder, "step": step, "min": min, "show_when": show_when}


PAYMENT_METHODS = model_choices(Income, "payment_method")


# ---------------------------------------------------------------- resource
class Resource:
    key = ""
    title = ""
    subtitle = ""
    module = ""
    viewset = None
    serializer = None
    item = "record"
    add_label = "Add"
    search_placeholder = "Search…"
    fields = []
    filters = []          # [{"key", "label", "choices": list|callable, "placeholder"}]
    period = False
    default_period = "month"
    vehicle_filters = False
    ordering = None
    preview = ""          # name of the live calculation in app.js
    empty_text = "Try a different month or clear the filters."

    # --- hooks (override when needed)
    def defaults(self, request):
        return {}

    def totals(self, t, count):
        return []

    def locked(self, obj):
        return None


    def queryset(self, vs):
        return vs.get_queryset()

    def extra_filter(self, qs, request):
        return qs

    def totals_queryset(self, qs):
        return qs

    def validate_extra(self, request, instance):
        """Checks for form fields that aren't part of the serializer. Return {field: message}."""
        return {}

    def snapshot(self, instance):
        """Values captured before saving, passed to after_save (to notice changes)."""
        return {}

    def after_save(self, obj, request, before, created):
        pass

    def get_serializer_class(self):
        return self.serializer or self.viewset.serializer_class

    def prepare(self, data, instance, request):
        """Adjust posted data before validation (e.g. fill fixed values)."""
        return data


# ---------------------------------------------------------------- modules
class Vehicles(Resource):
    key, module, item, add_label = "vehicles", "vehicles", "vehicle", "Add Vehicle"
    title = "Vehicles"
    subtitle = "Every bus, taxi, car and van in one list. Click a vehicle to see its full profile."
    viewset = VehicleViewSet
    search_placeholder = "Search number, name, model…"
    empty_text = "Add your first vehicle to get started."
    filters = [
        {"key": "vehicle_type", "label": "Vehicle type", "choices": vehicle_type_choices, "placeholder": "All vehicle types"},
        {"key": "status", "label": "Status", "choices": model_choices(Vehicle, "status"), "placeholder": "Any status"},
    ]
    fields = [
        F("registration_number", "Registration number", required=True, placeholder="TS09 UB 1234"),
        F("short_name", "Short name", placeholder="Bus 1, Taxi 2…", help="Optional. Used in reports instead of the number."),
        F("vehicle_type", "Vehicle type", "select", True, choices=active_vehicle_type_choices,
          help="Need another type? Add it in Settings."),
        F("status", "Current status", "select", True, choices=model_choices(Vehicle, "status")),
        F("brand", "Brand", placeholder="Tata, Toyota, Force…"),
        F("model", "Model", placeholder="Starbus, Innova Crysta…"),
        F("year", "Year", "number", step=1, min=1980),
        F("seating_capacity", "Seating capacity", "number", step=1),
        F("fuel_type", "Fuel type", "select", True, choices=model_choices(Vehicle, "fuel_type")),
        F("purchase_date", "Purchase date", "date"),
        F("purchase_price", "Purchase price", "money"),
        F("notes", "Notes", "textarea", half=False),
    ]

    def defaults(self, request):
        return {"status": "available", "fuel_type": "diesel"}


class Customers(Resource):
    key, module, item, add_label = "customers", "customers", "customer", "Add Customer"
    title = "Customers"
    subtitle = "Who books with you, and how much each customer still owes. Cancelled trips aren't counted."
    viewset = CustomerViewSet
    search_placeholder = "Search name, phone, company, GST…"
    filters = [{"key": "has_pending", "label": "Pending", "choices": [("true", "Only customers who owe money")],
                "placeholder": "All customers"}]
    fields = [
        F("name", "Customer name", required=True),
        F("phone", "Phone"),
        F("email", "Email"),
        F("company_name", "Company name"),
        F("gst_number", "GST number"),
        F("address", "Address", "textarea", half=False),
        F("notes", "Notes", "textarea", half=False),
    ]


class Bookings(Resource):
    key, module, item, add_label = "bookings", "bookings", "booking", "New Booking"
    title = "Bookings & Trips"
    subtitle = "Every booking with its balance. Balance = Booking amount − payments received."
    viewset = BookingViewSet
    search_placeholder = "Search booking no., customer, place…"
    period = True
    vehicle_filters = True
    preview = "booking"
    filters = [
        {"key": "trip_status", "label": "Trip status", "choices": model_choices(Booking, "trip_status"), "placeholder": "Any trip status"},
        {"key": "payment_status", "label": "Payment", "choices": [("due", "Money still due"), ("paid", "Paid"),
                                                                    ("partial", "Partially paid"), ("pending", "Pending")],
         "placeholder": "Any payment status"},
    ]
    fields = [
        F("customer", "Customer", "select", True, half=False, choices=customer_choices,
          help="New customer? Add them under Customers first."),
        F("vehicle", "Vehicle", "select", True, choices=vehicle_choices),
        F("driver", "Driver", "select", choices=driver_choices),
        F("trip_date", "Trip date", "date", True),
        F("return_date", "Return date", "date"),
        F("pickup_location", "Pickup location"),
        F("destination", "Destination"),
        F("number_of_days", "Number of days", "number", step=1, min=1, help="Worked out from the dates."),
        F("trip_status", "Trip status", "select", True, choices=model_choices(Booking, "trip_status")),
        F("booking_amount", "Booking amount", "money", True),
        F("advance", "Advance received", "money", help="Saved as income automatically."),
        F("advance_method", "Advance paid by", "select", choices=PAYMENT_METHODS),
        F("booking_date", "Booking / advance date", "date"),
        F("notes", "Notes", "textarea", half=False),
    ]

    def defaults(self, request):
        t = today().isoformat()
        return {"trip_date": t, "return_date": t, "number_of_days": 1, "trip_status": "upcoming",
                "booking_date": t, "advance_method": "cash", "advance": 0}

    def prepare(self, data, instance, request):
        if data.get("advance") in (None, ""):
            data["advance"] = 0
        return data

    def totals(self, t, count):
        booked, received = t.get("booking_amount") or 0, t.get("received") or 0
        return [("Trips", number(count), ""), ("Booked", rupees(booked), ""),
                ("Received", rupees(received), "t-in"), ("Still due", rupees(booked - received), "t-out")]


    def snapshot(self, instance):
        return {"driver": instance.driver_id, "trip_date": instance.trip_date, "vehicle": instance.vehicle_id,
                "status": instance.trip_status} if instance else {}

    def after_save(self, obj, request, before, created):
        from portal import services as ps
        url = f"/driver/trips/{obj.pk}/"
        if obj.driver_id and obj.driver_id != before.get("driver"):
            if not created:
                Booking.objects.filter(pk=obj.pk).update(accepted_at=None)
            ps.notify_driver(obj.driver, f"New trip assigned: {obj.booking_number} on {obj.trip_date:%d %b} "
                                         f"({obj.destination or obj.customer.name})", url)
            ps.log(obj.driver, "trip_assigned", f"Trip {obj.booking_number} assigned to {obj.driver.name}",
                   request.user, obj.vehicle, obj, status="assigned")
        elif obj.driver_id and not created and (before.get("trip_date") != obj.trip_date or
                                                before.get("vehicle") != obj.vehicle_id or
                                                before.get("status") != obj.trip_status):
            ps.notify_driver(obj.driver, f"Trip {obj.booking_number} was updated by the office", url)
        if before.get("driver") and before["driver"] != obj.driver_id:
            old = Driver.objects.filter(pk=before["driver"]).first()
            ps.notify_driver(old, f"Trip {obj.booking_number} is no longer assigned to you")


class Drivers(Resource):
    key, module, item, add_label = "drivers", "drivers", "driver", "Add Driver"
    title = "Drivers"
    subtitle = "Drivers, their vehicles, trips and salary. Click a driver to pay salary, advance or allowance."
    viewset = DriverViewSet
    search_placeholder = "Search name, phone, licence…"
    filters = [{"key": "status", "label": "Status", "choices": model_choices(Driver, "status"), "placeholder": "Any status"}]
    fields = [
        F("name", "Driver name", required=True),
        F("phone", "Phone"),
        F("licence_number", "Licence number"),
        F("licence_expiry", "Licence expiry", "date"),
        F("salary", "Monthly salary", "money"),
        F("joining_date", "Joining date", "date"),
        F("assigned_vehicle", "Assigned vehicle", "select", choices=vehicle_choices),
        F("status", "Status", "select", True, choices=model_choices(Driver, "status"),
          help="Inactive drivers can't log in to the app."),
        F("email", "Email"),
        F("blood_group", "Blood group", "select", choices=model_choices(Driver, "blood_group")),
        F("address", "Address", "textarea", half=False),
        F("emergency_contact_name", "Emergency contact name"),
        F("emergency_contact_phone", "Emergency contact phone"),
        F("photo", "Photo", "file", half=False),
        F("notes", "Notes", "textarea", half=False),
        F("login_username", "App login username", placeholder="e.g. 9876543210",
          help="Optional here. Fill both to create the driver's login now (manage it later from the driver's page)."),
        F("login_password", "App login password", "password", help="At least 8 characters."),
    ]

    def defaults(self, request):
        return {"status": "active", "joining_date": today().isoformat()}

    def validate_extra(self, request, instance):
        from accounts.models import User as U
        username, password = request.POST.get("login_username", "").strip(), request.POST.get("login_password", "")
        errors = {}
        if username or password:
            if instance is not None and instance.user_id:
                return {}
            if not username:
                errors["login_username"] = "Enter a username (the driver's phone number works well)."
            elif U.objects.filter(username__iexact=username).exists():
                errors["login_username"] = "That username is already taken."
            if len(password) < 8:
                errors["login_password"] = "The password needs at least 8 characters."
        return errors

    def snapshot(self, instance):
        return {"vehicle": instance.assigned_vehicle_id, "status": instance.status} if instance else {}

    def after_save(self, obj, request, before, created):
        from accounts.models import User as U
        from portal import services as ps
        username, password = request.POST.get("login_username", "").strip(), request.POST.get("login_password", "")
        if username and password and not obj.user_id:
            user = U.objects.create_user(username=username, password=password, first_name=obj.name,
                                         phone=obj.phone, email=obj.email, role="driver")
            obj.user = user
            obj.save(update_fields=["user"])
            ps.log(obj, "account_created", f"{request.user.display_name} created an app login for {obj.name}", request.user)
        if obj.user_id:   # inactive driver ⇒ login disabled
            U.objects.filter(pk=obj.user_id).update(is_active=obj.status != "inactive")
        if before.get("vehicle") != obj.assigned_vehicle_id:
            Driver.objects.filter(pk=obj.pk).update(assigned_vehicle=before.get("vehicle"))
            obj.assign_vehicle(obj.assigned_vehicle, by=request.user)
            if obj.assigned_vehicle:
                ps.log(obj, "vehicle_assigned", f"{obj.name} assigned to {obj.assigned_vehicle.label}", request.user,
                       obj.assigned_vehicle)
                ps.notify_driver(obj, f"You have been assigned vehicle {obj.assigned_vehicle.label}", "/driver/vehicle/")


class IncomeRes(Resource):
    key, module, item, add_label = "income", "income", "income entry", "Add Income"
    title = "Income"
    subtitle = "Money received from trips, rentals and other sources."
    viewset = IncomeViewSet
    search_placeholder = "Search customer, reference, booking…"
    period = True
    vehicle_filters = True
    preview = "income"
    filters = [
        {"key": "income_type", "label": "Income type", "choices": model_choices(Income, "income_type"), "placeholder": "All income types"},
        {"key": "payment_method", "label": "Payment method", "choices": PAYMENT_METHODS, "placeholder": "Any method"},
    ]
    fields = [
        F("date", "Date", "date", True),
        F("income_type", "Income type", "select", True, choices=model_choices(Income, "income_type")),
        F("booking", "Trip / booking", "select", half=False, choices=booking_choices,
          help="Choosing a booking fills the vehicle and customer, and reduces the booking's balance."),
        F("vehicle", "Vehicle", "select", choices=all_vehicle_choices),
        F("customer", "Customer", "select", choices=customer_choices),
        F("amount", "Amount received", "money", True),
        F("payment_method", "Payment method", "select", True, choices=PAYMENT_METHODS),
        F("reference", "Reference", placeholder="UPI ref, cheque no., receipt no."),
        F("notes", "Notes"),
    ]

    def defaults(self, request):
        d = {"date": today().isoformat(), "income_type": "trip", "payment_method": "cash"}
        booking_id = request.GET.get("booking")
        if booking_id:
            b = Booking.objects.with_totals().filter(pk=booking_id).first()
            if b:
                d.update(booking=b.pk, vehicle=b.vehicle_id, customer=b.customer_id,
                         amount=b.balance if b.balance > 0 else "")
        return d

    def totals(self, t, count):
        return [("Total received", rupees(t.get("amount")), "t-in"), ("Entries", number(count), "")]

    def locked(self, obj):
        if obj.is_booking_advance:
            return f"This is the advance from booking {obj.booking.booking_number}. Edit the booking to change it."
        return None


LINKED_SCREEN = {"fuel": "Fuel", "maintenance": "Maintenance", "driver": "Drivers", "emi": "Finance / EMI"}


class Expenses(Resource):
    key, module, item, add_label = "expenses", "expenses", "expense", "Add Expense"
    title = "Expenses"
    subtitle = "All money going out. Fuel, maintenance, salaries and EMI payments appear here automatically."
    viewset = ExpenseViewSet
    search_placeholder = "Search description or vehicle…"
    period = True
    vehicle_filters = True
    filters = [
        {"key": "category", "label": "Category", "choices": model_choices(Expense, "category"), "placeholder": "All categories"},
        {"key": "exclude_emi", "label": "EMI", "choices": [("true", "Without EMI")], "placeholder": "Including EMI"},
        {"key": "status", "label": "Approval", "choices": [("pending", "Pending approval"), ("approved", "Approved"),
                                                            ("rejected", "Rejected")], "placeholder": "Any approval status"},
    ]
    fields = [
        F("date", "Date", "date", True),
        F("category", "Expense category", "select", True,
          choices=[c for c in model_choices(Expense, "category") if c[0] not in ("fuel", "maintenance", "driver_salary", "emi")],
          help="Fuel, maintenance, salaries and EMI have their own screens."),
        F("vehicle", "Vehicle", "select", half=False, choices=all_vehicle_choices, help="Leave empty for office or general expenses."),
        F("amount", "Amount", "money", True),
        F("payment_method", "Payment method", "select", True, choices=PAYMENT_METHODS),
        F("description", "Description", half=False, placeholder="What was this for?"),
        F("receipt", "Receipt / attachment", "file", half=False),
    ]

    def defaults(self, request):
        return {"date": today().isoformat(), "category": "toll", "payment_method": "cash"}

    def queryset(self, vs):
        return Expense.all_objects.select_related("vehicle", "booking", "driver")

    def extra_filter(self, qs, request):
        st = request.GET.get("status")
        return qs.filter(status=st) if st else qs

    def totals_queryset(self, qs):
        return qs.filter(status="approved")

    def totals(self, t, count):
        return [("Total spent (approved)", rupees(t.get("amount")), "t-out"), ("Entries", number(count), "")]


    def locked(self, obj):
        if obj.status == "rejected":
            return "Rejected expenses can't be changed. They don't count in any totals."
        if obj.is_linked:
            return f"Added automatically from {LINKED_SCREEN.get(obj.source, obj.get_source_display())}. Change it there."
        return None


class Fuel(Resource):
    key, module, item, add_label = "fuel", "fuel", "fuel entry", "Fuel Entry"
    title = "Fuel"
    subtitle = "Every fill-up. Total is worked out as Litres × Rate and added to expenses."
    viewset = FuelEntryViewSet
    search_placeholder = "Search vehicle, driver, station…"
    period = True
    vehicle_filters = True
    preview = "fuel"
    fields = [
        F("date", "Date", "date", True),
        F("vehicle", "Vehicle", "select", True, choices=vehicle_choices),
        F("driver", "Driver", "select", choices=driver_choices),
        F("fuel_type", "Fuel type", "select", True, choices=model_choices(FuelEntry, "fuel_type")),
        F("booking", "Trip / booking", "select", half=False, choices=booking_choices, help="Optional. Lets you see fuel cost per trip."),
        F("litres", "Litres", "number", True),
        F("rate", "Rate per litre", "money", True),
        F("odometer", "Odometer (km)", "number", step=1),
        F("fuel_station", "Fuel station"),
        F("payment_method", "Payment method", "select", True, choices=PAYMENT_METHODS),
        F("notes", "Notes"),
    ]

    def defaults(self, request):
        return {"date": today().isoformat(), "fuel_type": "diesel", "payment_method": "cash"}

    def totals(self, t, count):
        litres, amount = t.get("litres") or 0, t.get("total_amount") or 0
        out = [("Total cost", rupees(amount), "t-out"), ("Litres", number(litres), ""), ("Fill-ups", number(count), "")]
        if litres:
            out.append(("Average rate", rupees(amount / litres), ""))
        return out


class MaintenanceRes(Resource):
    key, module, item, add_label = "maintenance", "maintenance", "maintenance record", "Add Maintenance"
    title = "Maintenance"
    subtitle = "Service and repair history. Total = Parts + Labour, added to expenses automatically."
    viewset = MaintenanceViewSet
    search_placeholder = "Search workshop, work done, vehicle…"
    period = True
    default_period = "year"
    vehicle_filters = True
    preview = "maintenance"
    filters = [
        {"key": "status", "label": "Status", "choices": model_choices(Maintenance, "status"), "placeholder": "Any status"},
        {"key": "maintenance_type", "label": "Type", "choices": model_choices(Maintenance, "maintenance_type"),
         "placeholder": "All work types"},
    ]

    def extra_filter(self, qs, request):
        st = request.GET.get("status")
        return qs.filter(status=st) if st else qs

    def totals_queryset(self, qs):
        return qs.filter(status="completed")

    def snapshot(self, instance):
        return {"status": instance.status} if instance else {}

    def after_save(self, obj, request, before, created):
        from portal import services as ps
        if obj.driver_id and before.get("status") and before["status"] != obj.status:
            ps.notify_driver(obj.driver, f"Your {obj.get_problem_category_display() or 'maintenance'} report on "
                                         f"{obj.vehicle.label} is now {obj.get_status_display()}", "/driver/maintenance/")
            ps.log(obj.driver, "maintenance_status", f"{request.user.display_name} set maintenance report to "
                                                     f"{obj.get_status_display()}", request.user, obj.vehicle,
                   amount=obj.total_cost if obj.status == "completed" else None, status=obj.status)
    fields = [
        F("vehicle", "Vehicle", "select", True, choices=vehicle_choices),
        F("date", "Date", "date", True),
        F("maintenance_type", "Maintenance type", "select", True, choices=model_choices(Maintenance, "maintenance_type")),
        F("workshop", "Workshop"),
        F("description", "Work done", "textarea", half=False),
        F("parts_cost", "Parts cost", "money"),
        F("labour_cost", "Labour cost", "money"),
        F("odometer", "Odometer (km)", "number", step=1),
        F("next_service_date", "Next service date", "date", help="You'll see a reminder on the dashboard."),
        F("status", "Status", "select", True, choices=model_choices(Maintenance, "status"),
          help="The cost is added to expenses only when the status is Completed."),
        F("priority", "Priority", "select", True, choices=model_choices(Maintenance, "priority")),
        F("problem_category", "Problem category", "select", choices=[c for c in model_choices(Maintenance, "problem_category") if c[0]]),
        F("payment_method", "Payment method", "select", True, choices=PAYMENT_METHODS),
        F("notes", "Notes"),
    ]

    def defaults(self, request):
        return {"date": today().isoformat(), "maintenance_type": "service", "payment_method": "cash",
                "status": "completed", "priority": "medium"}

    def prepare(self, data, instance, request):
        for k in ("parts_cost", "labour_cost"):
            if data.get(k) in (None, ""):
                data[k] = 0
        return data

    def totals(self, t, count):
        return [("Total cost (completed)", rupees(t.get("total_cost")), "t-out"), ("Jobs", number(count), "")]


class Documents(Resource):
    key, module, item, add_label = "documents", "documents", "document", "Add Document"
    title = "Documents"
    subtitle = "Insurance, permit, fitness, road tax and pollution — with expiry alerts."
    viewset = VehicleDocumentViewSet
    search_placeholder = "Search vehicle or number…"
    vehicle_filters = True
    ordering = "expiry_date"
    empty_text = "Add insurance, permit and fitness dates so FleetBooks can remind you before they expire."
    filters = [
        {"key": "status", "label": "Status", "choices": [("attention", "Expired or due in 30 days"), ("expired", "Expired"),
                                                          ("7_days", "Expires in 7 days"), ("30_days", "Expires in 30 days"),
                                                          ("valid", "Valid")], "placeholder": "Any status"},
        {"key": "doc_type", "label": "Document", "choices": model_choices(VehicleDocument, "doc_type"), "placeholder": "All documents"},
    ]
    fields = [
        F("vehicle", "Vehicle", "select", True, half=False, choices=vehicle_choices),
        F("doc_type", "Document", "select", True, choices=model_choices(VehicleDocument, "doc_type")),
        F("document_number", "Policy / document number"),
        F("provider", "Issued by", placeholder="Insurance company, RTO…"),
        F("amount", "Renewal amount", "money", help="Shown under Upcoming Payments when renewal is near."),
        F("issue_date", "Issue date", "date"),
        F("expiry_date", "Expiry / due date", "date", True),
        F("file", "Copy of document", "file", half=False),
        F("notes", "Notes", "textarea", half=False),
    ]

    def defaults(self, request):
        return {"doc_type": "insurance"}


class Loans(Resource):
    key, module, item, add_label = "loans", "finance", "loan", "Add Loan"
    title = "Loans"
    viewset = LoanViewSet
    preview = "loan"
    fields = [
        F("vehicle", "Vehicle", "select", True, choices=vehicle_choices),
        F("lender", "Finance company / bank", required=True),
        F("account_number", "Loan account number"),
        F("loan_amount", "Loan amount", "money", True),
        F("down_payment", "Down payment", "money"),
        F("interest_rate", "Interest rate (% per year)", "number", True, step="0.01"),
        F("tenure_months", "Loan tenure (months)", "number", True, step=1, min=1),
        F("start_date", "Loan start date", "date", True),
        F("first_emi_date", "First EMI due date", "date", help="Leave empty for one month after the start date."),
        F("emi_amount", "EMI amount", "money", help="Leave empty to calculate it automatically."),
        F("notes", "Notes", "textarea", half=False),
    ]

    def defaults(self, request):
        return {"start_date": today().isoformat(), "down_payment": 0}

    def prepare(self, data, instance, request):
        if data.get("down_payment") in (None, ""):
            data["down_payment"] = 0
        return data


class DriverPayments(Resource):
    key, module, item, add_label = "driver-payments", "driver_payments", "payment", "Pay salary / advance"
    title = "Driver payments"
    viewset = DriverPaymentViewSet
    fields = [
        F("driver", "Driver", "select", True, choices=driver_choices),
        F("date", "Date paid", "date", True),
        F("payment_type", "Payment type", "select", True, choices=model_choices(DriverPayment, "payment_type")),
        F("salary_month", "For salary of (any date in that month)", "date",
          help="Advances are deducted from this month's salary. Not used for allowances."),
        F("amount", "Amount", "money", True),
        F("payment_method", "Payment method", "select", True, choices=PAYMENT_METHODS),
        F("notes", "Notes", placeholder="e.g. Bata for Tirupati trip"),
    ]

    def defaults(self, request):
        t = today()
        d = {"date": t.isoformat(), "payment_type": "salary", "payment_method": "cash",
             "salary_month": t.replace(day=1).isoformat()}
        if request.GET.get("driver"):
            d["driver"] = request.GET["driver"]
        if request.GET.get("amount"):
            d["amount"] = request.GET["amount"]
        return d

    def prepare(self, data, instance, request):
        # Store the first day of the chosen month.
        sm = data.get("salary_month")
        if sm:
            data["salary_month"] = str(sm)[:8] + "01"
        return data


class VehicleTypes(Resource):
    key, module, item, add_label = "vehicle-types", "vehicle_types", "vehicle type", "Add type"
    title = "Vehicle types"
    subtitle = ("Every screen, calculation and report works the same for all types. "
                "Add a new type any time — nothing else needs to change.")
    viewset = VehicleTypeViewSet
    fields = [
        F("name", "Type name", required=True, half=False, placeholder="e.g. Mini Bus, Tempo Traveller, Truck"),
        F("sort_order", "Position in lists", "number", step=1, help="Lower numbers appear first."),
        F("is_active", "Show in the vehicle form", "checkbox"),
    ]

    def defaults(self, request):
        last = VehicleType.objects.order_by("-sort_order").first()
        return {"is_active": True, "sort_order": (last.sort_order + 10) if last else 10}


class Users(Resource):
    key, module, item, add_label = "users", "users", "user", "Add user"
    title = "Users & roles"
    subtitle = "Who can log in, and what each person may do."
    serializer = UserSerializer
    fields = [
        F("first_name", "Name", required=True),
        F("username", "Login username", required=True),
        F("role", "Role", "select", True, choices=model_choices(User, "role")),
        F("phone", "Phone"),
        F("email", "Email"),
        F("password", "Password", "password", help="At least 8 characters. When editing, leave empty to keep the current password."),
        F("is_active", "Account active (can log in)", "checkbox"),
    ]
    search_placeholder = "Search name or username…"

    class _VS:
        search_fields = ["username", "first_name", "last_name", "email"]
        sum_fields = ()
        filterset_class = None
        filterset_fields = None
        protected_message = "This user has records linked to them and can't be deleted. Disable the account instead."

        def get_queryset(self):
            return User.objects.all().order_by("username")

        def check_can_delete(self, instance):
            return None

    viewset = _VS

    def defaults(self, request):
        return {"role": "staff", "is_active": True}

    def prepare(self, data, instance, request):
        if not data.get("password"):
            data.pop("password", None)
        return data

    def locked(self, obj):
        return None


RESOURCES = {r.key: r for r in [Vehicles(), Customers(), Bookings(), Drivers(), IncomeRes(), Expenses(), Fuel(),
                                   MaintenanceRes(), Documents(), Loans(), DriverPayments(), VehicleTypes(), Users()]}


