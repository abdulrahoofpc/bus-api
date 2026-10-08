"""
OpenAPI descriptions for the function-based endpoints, kept in one place so the view code stays plain.
Shown at /api/docs/ (Swagger UI) and /api/schema/ (OpenAPI 3 YAML). Imported by config/urls.py.

Viewset endpoints (/api/vehicles/, /api/bookings/, …) are described automatically from their serializers.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers as s

from accounts import views as auth_views
from accounts.serializers import BusinessSettingsSerializer
from office import api as office
from portal import api as driver
from reports import views as reports

OBJ = OpenApiTypes.OBJECT
ID = lambda name, required=True, help="": s.IntegerField(required=required, help_text=help or f"{name} id")  # noqa: E731
DATE = lambda required=True, help="YYYY-MM-DD": s.DateField(required=required, help_text=help)  # noqa: E731
TEXT = lambda required=False, help="": s.CharField(required=required, help_text=help)  # noqa: E731
MONEY = lambda required=True, help="": s.DecimalField(max_digits=12, decimal_places=2, required=required, help_text=help)  # noqa: E731
FILE = lambda help="Photo (jpg/png/webp/heic), PDF or short video, max 10 MB": s.FileField(required=False, help_text=help)  # noqa: E731
ERR = {400: OBJ, 401: OBJ, 403: OBJ, 404: OBJ}


def body(name, fields, files=False):
    ser = inline_serializer(name=name, fields=fields)
    return {"multipart/form-data": ser, "application/json": ser} if files else ser


def doc(view, method=None, **kw):
    """Attach a description to a function view (all its methods, or one)."""
    kw.setdefault("responses", {200: OBJ, **ERR})
    extend_schema(methods=[method] if method else None, **kw)(view)


def q(name, type_=str, help=""):
    return OpenApiParameter(name, type_, OpenApiParameter.QUERY, required=False, description=help)


PERIOD = [q("month", int, "1-12"), q("year", int, "e.g. 2026"), q("date_from", OpenApiTypes.DATE), q("date_to", OpenApiTypes.DATE),
          q("vehicle", int, "vehicle id"), q("vehicle_type", int, "vehicle type id")]

# ------------------------------------------------------------------ auth
doc(auth_views.me, tags=["auth"], summary="Who am I: user, role, and what the role may see or change")
doc(auth_views.change_password, tags=["auth"], summary="Change my password (office users)",
    request=body("ChangePassword", {"current_password": TEXT(True), "new_password": TEXT(True, "at least 8 characters")}))
extend_schema(tags=["auth"], request=BusinessSettingsSerializer, responses={200: BusinessSettingsSerializer, **ERR},
              summary="Business name, address, GST… printed on reports")(auth_views.BusinessSettingsView)

# ------------------------------------------------------------------ driver API
T = ["driver"]
doc(driver.dashboard, tags=T, summary="Driver home: name, vehicle, current trip, month stats, unread notifications")
doc(driver.trips, tags=T, summary="My trips", parameters=[q("show", str, "active (default) | completed | all")])
doc(driver.trip_detail, tags=T, operation_id="driver_trip_detail", summary="One of my trips (404 if it isn't mine)")
doc(driver.trip_accept, tags=T, summary="Accept an assigned trip", request=None, responses={200: OBJ, 409: OBJ, **ERR})
doc(driver.trip_start, tags=T, summary="Start a trip (vehicle becomes On Trip)", responses={200: OBJ, 409: OBJ, **ERR},
    request=body("TripStart", {"start_odometer": s.IntegerField(min_value=0), "start_location": TEXT(True),
                               "started_at": s.DateTimeField(required=False, help_text="default: now")}))
doc(driver.trip_update, tags=T, summary="Add notes or a document during a trip", responses={200: OBJ, 409: OBJ, **ERR},
    request=body("TripUpdate", {"trip_notes": TEXT(), "trip_document": FILE()}, files=True))
doc(driver.trip_complete, tags=T, summary="Complete a trip (KM = end − start odometer)", responses={200: OBJ, 409: OBJ, **ERR},
    request=body("TripComplete", {"end_odometer": s.IntegerField(min_value=0, help_text="at least the starting reading"),
                                  "end_location": TEXT(True), "completed_at": s.DateTimeField(required=False, help_text="default: now"),
                                  "trip_notes": TEXT(), "extra_category": TEXT(help="optional extra expense category"),
                                  "extra_amount": MONEY(False), "extra_receipt": FILE(), "trip_document": FILE()}, files=True))
doc(driver.fuel, "GET", tags=T, summary="My fuel entries")
doc(driver.fuel, "POST", tags=T, summary="Add fuel (Litres × Rate, counts straight away)", responses={201: OBJ, **ERR},
    request=body("DriverFuel", {"vehicle": ID("vehicle"), "booking": ID("trip", False), "date": DATE(),
                                "fuel_type": TEXT(True, "diesel | petrol | cng | …"), "litres": MONEY(), "rate": MONEY(),
                                "odometer": s.IntegerField(required=False), "fuel_station": TEXT(),
                                "payment_method": TEXT(True, "cash | upi | card | …"), "receipt": FILE(), "notes": TEXT()}, files=True))
doc(driver.expenses, "GET", tags=T, summary="My expenses", parameters=[q("status", str, "pending | approved | rejected")])
doc(driver.expenses, "POST", tags=T, summary="Submit an expense (starts Pending, needs office approval)", responses={201: OBJ, **ERR},
    request=body("DriverExpense", {"date": DATE(), "vehicle": ID("vehicle"), "booking": ID("trip", False),
                                   "category": TEXT(True, "toll | parking | food_allowance | …"), "amount": MONEY(),
                                   "description": TEXT(), "payment_method": TEXT(True), "receipt": FILE(), "notes": TEXT()}, files=True))
doc(driver.expense_detail, tags=T, operation_id="driver_expense_detail", summary="One of my expenses")
doc(driver.expense_withdraw, tags=T, summary="Withdraw a pending expense", request=None, responses={204: None, 409: OBJ, **ERR})
doc(driver.maintenance, "GET", tags=T, summary="My problem reports")
doc(driver.maintenance, "POST", tags=T, summary="Report a vehicle problem", responses={201: OBJ, **ERR},
    request=body("DriverProblem", {"vehicle": ID("vehicle"), "date": DATE(), "problem_category": TEXT(True, "engine | tyre | brake | …"),
                                   "priority": TEXT(True, "low | medium | high | emergency"), "description": TEXT(True),
                                   "odometer": s.IntegerField(required=False), "photo": FILE(), "notes": TEXT()}, files=True))
doc(driver.maintenance_detail, tags=T, operation_id="driver_problem_detail", summary="One of my problem reports")
doc(driver.vehicle, tags=T, summary="My vehicle, its documents and assignment history")
doc(driver.profile, "GET", tags=T, summary="My profile")
doc(driver.profile, "PATCH", tags=T, summary="Update my profile (office-only fields like name and salary are ignored)",
    request=body("DriverProfile", {"phone": TEXT(), "email": TEXT(), "address": TEXT(), "blood_group": TEXT(),
                                   "emergency_contact_name": TEXT(), "emergency_contact_phone": TEXT(), "licence_number": TEXT(),
                                   "licence_expiry": DATE(False), "photo": FILE("jpg/png/webp/heic"),
                                   "photo-clear": s.BooleanField(required=False, help_text="true removes the photo")}, files=True))
doc(driver.change_password, tags=T, summary="Change my password",
    request=body("DriverPassword", {"current_password": TEXT(True), "new_password": TEXT(True, "at least 8 characters"),
                                    "confirm": TEXT(True, "same as new_password")}))
doc(driver.notifications, tags=T, summary="My notifications (marks them read)")
doc(driver.form_options, tags=T, summary="Choices for the driver forms: my vehicle, my trips, categories, fuel types…")

# ------------------------------------------------------------------ office API
O = ["office"]
doc(office.dashboard, tags=O, summary="Office home: today on the road, needs attention, month ledger, driver numbers")
doc(office.resource_defs, tags=O, summary="Definitions of every module the role may open (fields, choices, filters, totals)")
doc(office.resource_list, "GET", tags=O, operation_id="office_record_list", summary="List a module with search, filters, period and totals",
    parameters=[OpenApiParameter("key", str, OpenApiParameter.PATH,
                                 description="vehicles | customers | bookings | drivers | income | expenses | fuel | maintenance | "
                                             "documents | loans | driver-payments | vehicle-types | users"),
                q("search"), q("mode", str, "month | year | range | all"), q("month", int), q("year", int),
                q("from", OpenApiTypes.DATE), q("to", OpenApiTypes.DATE), q("page", int), q("page_size", int, "max 200"),
                q("status"), q("vehicle", int), q("driver", int)])
doc(office.resource_list, "POST", tags=O, operation_id="office_record_create", summary="Add a record (fields from /api/office/resources/; multipart or JSON)",
    request={"multipart/form-data": OBJ, "application/json": OBJ}, responses={201: OBJ, **ERR})
doc(office.resource_detail, "GET", tags=O, operation_id="office_record_detail", summary="One record")
for m in ("POST", "PATCH"):
    doc(office.resource_detail, m, tags=O, operation_id=f"office_record_{m.lower()}", summary="Save changes (only the fields sent change)",
        request={"multipart/form-data": OBJ, "application/json": OBJ})
doc(office.resource_detail, "DELETE", tags=O, operation_id="office_record_delete", summary="Delete (refused with 400 when linked records exist)", responses={204: None, **ERR})
doc(office.expense_approvals, tags=O, summary="Driver expenses waiting for approval, and recently reviewed ones")
doc(office.expense_decide, tags=O, summary="Approve or reject a driver expense",
    request=body("ExpenseDecision", {"action": s.ChoiceField(choices=["approve", "reject"]), "reason": TEXT(help="required to reject")}))
doc(office.driver_overview_api, tags=O, summary="Driver profile, month numbers, salary, trips, fuel, expenses, reports, history",
    parameters=[q("month", str, "YYYY-MM, default this month")])
doc(office.driver_account, tags=O, summary="Create, reset, activate or deactivate a driver's app login",
    request=body("DriverAccount", {"action": s.ChoiceField(choices=["create", "reset", "activate", "deactivate"]),
                                   "username": TEXT(help="for create"), "password": TEXT(help="for create / reset")}))
doc(office.assign_vehicle, tags=O, summary="Assign a vehicle to a driver (history is kept)",
    request=body("AssignVehicle", {"vehicle": ID("vehicle", False, "empty = no vehicle"), "start_date": DATE(False), "notes": TEXT()}))
doc(office.assign_trip, "GET", tags=O, summary="Trips without a driver, and the drivers who can take them")
doc(office.assign_trip, "POST", tags=O, summary="Assign a trip to a driver (the driver is notified)",
    request=body("AssignTrip", {"booking": ID("booking"), "driver": ID("driver")}))
doc(office.activity, tags=O, summary="Driver activity log", parameters=[q("driver", int), q("q", str, "search text"), q("page", int)])
doc(office.notifications, tags=O, summary="My notifications", parameters=[q("mark_read", str, "1 (default) marks them read, 0 keeps them")])
doc(office.unread, tags=O, summary="Number of unread notifications")

# ------------------------------------------------------------------ reports
R = ["reports"]
doc(reports.dashboard, tags=R, summary="Dashboard figures")
doc(reports.monthly_finance, tags=R, summary="Monthly finance: income, expenses, EMI, profit", parameters=PERIOD)
doc(reports.report_list, tags=R, operation_id="report_list", summary="Available reports")
doc(reports.report_run, tags=R, operation_id="report_run", summary="Run a report (JSON), or download it with export=pdf|xlsx",
    parameters=PERIOD + [q("driver", int), q("export", str, "pdf | xlsx")],
    responses={(200, "application/json"): OBJ, (200, "application/pdf"): OpenApiTypes.BINARY,
               (200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): OpenApiTypes.BINARY, **ERR})
