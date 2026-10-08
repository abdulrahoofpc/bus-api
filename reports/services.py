"""
All business calculations live here so the dashboard, monthly finance screen,
vehicle profiles and reports always show the same numbers.

Definitions used everywhere:
  Income   = every Income entry (trip payments, advances, rentals, other)
  Expenses = every Expense entry EXCEPT the EMI category
  EMI      = Expense entries in the EMI category (created when an EMI is paid)
  Profit   = Income - Expenses - EMI
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, F, Q, Sum

from accounts.permissions import can_see_finance
from bookings.models import Booking
from common.dates import add_months, month_bounds, today
from common.money import money
from fleet.models import EXPIRY_LABELS, Driver, Vehicle, VehicleDocument
from finance.models import EMIInstallment, Expense, FuelEntry, Income, Loan, Maintenance

ZERO = Decimal("0")
EMI = Expense.Category.EMI


def s(qs, field="amount"):
    return money(qs.aggregate(t=Sum(field))["t"] or ZERO)


def grouped(qs, key, field="amount"):
    """{key: total} for a queryset grouped by one column."""
    return {k: money(v) for k, v in qs.values_list(key).annotate(t=Sum(field)).order_by()}


def vehicle_filter(qs, params, path="vehicle"):
    """Apply ?vehicle= and ?vehicle_type= to any queryset."""
    if params.get("vehicle"):
        qs = qs.filter(**{f"{path}_id": params["vehicle"]})
    if params.get("vehicle_type"):
        qs = qs.filter(**{f"{path}__vehicle_type_id": params["vehicle_type"]})
    return qs


def incomes(start, end, params=None):
    return vehicle_filter(Income.objects.filter(date__range=(start, end)), params or {})


def expenses(start, end, params=None, include_emi=False):
    qs = vehicle_filter(Expense.objects.filter(date__range=(start, end)), params or {})
    return qs if include_emi else qs.exclude(category=EMI)


def emi_paid(start, end, params=None):
    return vehicle_filter(Expense.objects.filter(date__range=(start, end), category=EMI), params or {})


def period_totals(start, end, params=None):
    income = s(incomes(start, end, params))
    expense = s(expenses(start, end, params))
    emi = s(emi_paid(start, end, params))
    return {"income": income, "expenses": expense, "emi": emi, "profit": income - expense - emi}


def vehicle_breakdown(start, end, params=None):
    """One row per vehicle: income, expense, EMI, profit. Plus a row for amounts not tied to a vehicle."""
    params = params or {}
    inc = grouped(incomes(start, end, params), "vehicle")
    exp = grouped(expenses(start, end, params), "vehicle")
    emi = grouped(emi_paid(start, end, params), "vehicle")
    trips = dict(
        vehicle_filter(Booking.objects.filter(trip_date__range=(start, end)).exclude(trip_status="cancelled"), params)
        .values_list("vehicle").annotate(c=Count("id"))
    )
    active_ids = set(inc) | set(exp) | set(emi) | set(trips)
    vehicles = Vehicle.objects.select_related("vehicle_type").filter(
        Q(id__in=[i for i in active_ids if i]) | ~Q(status__in=["sold", "inactive"]))
    if params.get("vehicle_type"):
        vehicles = vehicles.filter(vehicle_type_id=params["vehicle_type"])
    if params.get("vehicle"):
        vehicles = Vehicle.objects.filter(pk=params["vehicle"]).select_related("vehicle_type")
    rows = []
    for v in vehicles:
        i, e, m = inc.get(v.id, ZERO), exp.get(v.id, ZERO), emi.get(v.id, ZERO)
        rows.append({
            "vehicle_id": v.id, "vehicle": v.short_name or v.registration_number,
            "registration_number": v.registration_number, "vehicle_type": v.vehicle_type.name,
            "trips": trips.get(v.id, 0), "income": i, "expenses": e, "emi": m, "profit": i - e - m,
        })
    general = None
    if not params.get("vehicle") and not params.get("vehicle_type"):
        i, e, m = inc.get(None, ZERO), exp.get(None, ZERO), emi.get(None, ZERO)
        if i or e or m:
            general = {"vehicle_id": None, "vehicle": "Office / not linked to a vehicle", "registration_number": "",
                       "vehicle_type": "", "trips": 0, "income": i, "expenses": e, "emi": m, "profit": i - e - m}
    return rows, general


def pending_bookings():
    return (Booking.objects.with_totals().exclude(trip_status="cancelled")
            .filter(received__lt=F("booking_amount")).select_related("customer", "vehicle"))


def salary_status(driver, month_start):
    paid = driver.payments.filter(salary_month=month_start)
    salary_paid = s(paid.filter(payment_type="salary"))
    advance = s(paid.filter(payment_type="advance"))
    allowance = s(paid.filter(payment_type="allowance"))
    return {"salary": driver.salary, "salary_paid": salary_paid, "advance": advance, "allowance": allowance,
            "balance": max(driver.salary - salary_paid - advance, ZERO)}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def dashboard(user):
    t = today()
    month_start, month_end = month_bounds(t.year, t.month)
    soon = t + timedelta(days=30)

    status_counts = dict(Vehicle.objects.values_list("status").annotate(c=Count("id")))
    fleet = {k: status_counts.get(k, 0) for k in ("available", "on_trip", "maintenance", "inactive", "sold")}
    fleet["total"] = sum(v for k, v in fleet.items() if k != "sold")

    by_type = list(Vehicle.objects.exclude(status="sold").values("vehicle_type__name")
                   .annotate(count=Count("id")).order_by("vehicle_type__sort_order"))

    trips = Booking.objects.select_related("customer", "vehicle")
    ongoing = trips.filter(trip_status="ongoing").order_by("trip_date")[:8]
    upcoming = trips.filter(trip_status="upcoming", trip_date__range=(t, t + timedelta(days=7))).order_by("trip_date")[:8]

    def trip_row(b):
        return {"id": b.id, "booking_number": b.booking_number, "customer": b.customer.name,
                "vehicle": b.vehicle.label, "trip_date": b.trip_date, "return_date": b.return_date,
                "destination": b.destination, "trip_status": b.trip_status}

    docs = VehicleDocument.objects.filter(expiry_date__lte=soon).select_related("vehicle").order_by("expiry_date")
    documents = [{"id": d.id, "vehicle_id": d.vehicle_id, "vehicle": d.vehicle.label, "item": d.get_doc_type_display(),
                  "date": d.expiry_date, "days_left": d.days_left, "status": d.status,
                  "status_label": EXPIRY_LABELS[d.status]} for d in docs[:12]]
    for drv in Driver.objects.filter(status="active", licence_expiry__lte=soon).order_by("licence_expiry")[:5]:
        documents.append({"id": None, "vehicle_id": None, "vehicle": drv.name, "item": "Driving licence",
                          "date": drv.licence_expiry, "days_left": (drv.licence_expiry - t).days,
                          "status": drv.licence_status, "status_label": EXPIRY_LABELS[drv.licence_status]})
    documents.sort(key=lambda d: d["date"])

    service_due = [
        {"vehicle_id": m.vehicle_id, "vehicle": m.vehicle.label, "date": m.next_service_date,
         "days_left": (m.next_service_date - t).days, "item": m.get_maintenance_type_display()}
        for m in latest_service_due(soon)
    ]

    data = {
        "today": t, "month_label": f"{month_start:%B %Y}", "fleet": fleet, "vehicles_by_type": by_type,
        "ongoing_trips": [trip_row(b) for b in ongoing], "upcoming_trips": [trip_row(b) for b in upcoming],
        "documents": documents, "service_due": service_due, "finance": None,
    }
    if not can_see_finance(user):
        return data

    month = period_totals(month_start, month_end)
    pending = pending_bookings()
    pending_total = money(sum((b.booking_amount - b.received for b in pending), ZERO))
    emi_due = (EMIInstallment.objects.filter(paid_date__isnull=True, due_date__lte=soon)
               .select_related("loan", "loan__vehicle").order_by("due_date"))
    salaries = []
    for drv in Driver.objects.filter(status="active", salary__gt=0):
        st = salary_status(drv, month_start)
        if st["balance"] > 0:
            salaries.append({"driver_id": drv.id, "driver": drv.name, "amount": st["balance"]})
    bills = [{"vehicle": d.vehicle.label, "item": d.get_doc_type_display(), "date": d.expiry_date,
              "amount": d.amount, "status": d.status}
             for d in docs.exclude(amount__isnull=True).exclude(doc_type="other")[:6]]

    data["finance"] = {
        "today_income": s(Income.objects.filter(date=t)),
        "today_expenses": s(Expense.objects.filter(date=t).exclude(category=EMI)),
        "month": month,
        "pending_payments": {"total": pending_total, "count": len(pending)},
        "upcoming_emi": {"total": s(emi_due), "count": emi_due.count(),
                         "overdue": emi_due.filter(due_date__lt=t).count()},
        "customer_pending": [
            {"booking_id": b.id, "booking_number": b.booking_number, "customer": b.customer.name,
             "trip_date": b.trip_date, "amount": money(b.booking_amount - b.received)}
            for b in sorted(pending, key=lambda b: b.trip_date)[:6]
        ],
        "emi_due": [
            {"id": i.id, "loan_id": i.loan_id, "vehicle": i.loan.vehicle.label, "lender": i.loan.lender,
             "due_date": i.due_date, "amount": i.amount, "status": i.status} for i in emi_due[:6]
        ],
        "salary_due": salaries[:6],
        "salary_due_total": sum((x["amount"] for x in salaries), ZERO),
        "other_bills": bills,
    }
    return data


def latest_service_due(until):
    """For each vehicle, its most recent maintenance record's next service date, if due by `until`."""
    latest = {}
    for m in (Maintenance.objects.filter(next_service_date__isnull=False, status="completed").select_related("vehicle")
              .exclude(vehicle__status__in=["sold", "inactive"]).order_by("vehicle_id", "-date", "-id")):
        latest.setdefault(m.vehicle_id, m)
    return sorted((m for m in latest.values() if m.next_service_date <= until), key=lambda m: m.next_service_date)


# ---------------------------------------------------------------------------
# Monthly finance
# ---------------------------------------------------------------------------
def monthly_finance(start, end, label, params):
    totals = period_totals(start, end, params)
    rows, general = vehicle_breakdown(start, end, params)
    income_by_type = [
        {"label": dict(Income.IncomeType.choices)[k], "amount": v}
        for k, v in sorted(grouped(incomes(start, end, params), "income_type").items(), key=lambda x: -x[1])
    ]
    expense_by_category = [
        {"label": dict(Expense.Category.choices)[k], "amount": v}
        for k, v in sorted(grouped(expenses(start, end, params), "category").items(), key=lambda x: -x[1])
    ]
    by_type = {}
    for r in rows:
        agg = by_type.setdefault(r["vehicle_type"], {"vehicle_type": r["vehicle_type"], "vehicles": 0,
                                                     "income": ZERO, "expenses": ZERO, "emi": ZERO, "profit": ZERO})
        agg["vehicles"] += 1
        for k in ("income", "expenses", "emi", "profit"):
            agg[k] += r[k]
    trips = vehicle_filter(Booking.objects.filter(trip_date__range=(start, end)), params)
    return {
        "period": {"start": start, "end": end, "label": label},
        "totals": totals,
        "vehicles": sorted(rows, key=lambda r: -r["profit"]),
        "general": general,
        "by_vehicle_type": list(by_type.values()),
        "income_by_type": income_by_type,
        "expense_by_category": expense_by_category,
        "trips": {
            "total": trips.exclude(trip_status="cancelled").count(),
            "completed": trips.filter(trip_status="completed").count(),
            "cancelled": trips.filter(trip_status="cancelled").count(),
        },
        "fuel": {"litres": s(vehicle_filter(FuelEntry.objects.filter(date__range=(start, end)), params), "litres")},
    }


# ---------------------------------------------------------------------------
# Vehicle profile, trip and driver summaries
# ---------------------------------------------------------------------------
def _rows(qs, fields, limit=100):
    return list(qs.values(*fields)[:limit])


def vehicle_profile(vehicle, user, params):
    t = today()
    month_start, month_end = month_bounds(t.year, t.month)
    vp = {"vehicle": vehicle.id}
    far_past, far_future = date(1990, 1, 1), date(2100, 12, 31)
    docs = [{"id": d.id, "doc_type": d.get_doc_type_display(), "document_number": d.document_number,
             "provider": d.provider, "expiry_date": d.expiry_date, "status": d.status,
             "status_label": EXPIRY_LABELS[d.status], "days_left": d.days_left,
             "file": d.file.url if d.file else None} for d in vehicle.documents.all()]
    trips = [{"id": b.id, "booking_number": b.booking_number, "customer": b.customer.name,
              "trip_date": b.trip_date, "return_date": b.return_date, "destination": b.destination,
              "booking_amount": b.booking_amount, "balance": b.balance,
              "trip_status": b.get_trip_status_display(), "trip_status_code": b.trip_status}
             for b in vehicle.bookings.with_totals().select_related("customer")[:100]]
    drivers = list(vehicle.drivers.values("id", "name", "phone"))
    data = {"documents": docs, "trips": trips, "drivers": drivers, "finance": None}
    if not can_see_finance(user):
        return data

    lifetime = period_totals(far_past, far_future, vp)
    this_month = period_totals(month_start, month_end, vp)
    loans = [{"id": ln.id, "lender": ln.lender, "loan_amount": ln.loan_amount, "emi_amount": ln.emi_amount,
              "tenure_months": ln.tenure_months, "outstanding": ln.outstanding,
              "paid": ln.installments.filter(paid_date__isnull=False).count(), "is_closed": ln.is_closed}
             for ln in vehicle.loans.all()]
    fuel = vehicle.fuel_entries.all()
    data["finance"] = {
        "lifetime": lifetime,
        "this_month": this_month,
        "fuel_total": s(fuel, "total_amount"),
        "fuel_litres": s(fuel, "litres"),
        "maintenance_total": s(vehicle.maintenance_records.all(), "total_cost"),
        "outstanding_loans": sum((ln["outstanding"] for ln in loans if not ln["is_closed"]), ZERO),
        "loans": loans,
        "income": _rows(vehicle.incomes.select_related("customer").order_by("-date"),
                        ["id", "date", "income_type", "amount", "payment_method", "customer__name", "reference"]),
        "expenses": _rows(vehicle.expenses.order_by("-date"),
                          ["id", "date", "category", "amount", "description", "source"]),
        "fuel": _rows(fuel.order_by("-date"),
                      ["id", "date", "litres", "rate", "total_amount", "odometer", "fuel_station"]),
        "maintenance": _rows(vehicle.maintenance_records.order_by("-date"),
                             ["id", "date", "maintenance_type", "workshop", "total_cost", "next_service_date"]),
        "monthly": [
            {"label": f"{m:%b %Y}", **period_totals(*month_bounds(m.year, m.month), vp)}
            for m in (add_months(month_start, -i) for i in range(5, -1, -1))
        ],
    }
    return data


def trip_costs(booking):
    return s(booking.expenses.exclude(category=EMI))


def trip_summary(booking, user):
    data = {"booking_id": booking.id}
    if not can_see_finance(user):
        return data
    costs = trip_costs(booking)
    data.update({
        "received": booking.get_received(),
        "balance": booking.balance,
        "costs": costs,
        "profit": booking.booking_amount - costs if booking.trip_status != "cancelled" else booking.get_received() - costs,
        "payments": _rows(booking.incomes.order_by("date"), ["id", "date", "income_type", "amount", "payment_method", "reference"]),
        "expenses": _rows(booking.expenses.order_by("date"), ["id", "date", "category", "amount", "description"]),
    })
    return data


def driver_summary(driver, user, params):
    t = today()
    try:
        year, month = int(params.get("year") or t.year), int(params.get("month") or t.month)
    except ValueError:
        year, month = t.year, t.month
    month_start, _ = month_bounds(year, month)
    trips = driver.bookings.select_related("customer", "vehicle").order_by("-trip_date")
    data = {
        "month_label": f"{month_start:%B %Y}",
        "trips": [{"id": b.id, "booking_number": b.booking_number, "trip_date": b.trip_date,
                   "customer": b.customer.name, "vehicle": b.vehicle.label, "destination": b.destination,
                   "trip_status": b.get_trip_status_display()} for b in trips[:50]],
        "salary": None,
    }
    if can_see_finance(user) or user.role == "manager":
        data["salary"] = salary_status(driver, month_start)
        data["payments"] = _rows(driver.payments.order_by("-date"),
                                 ["id", "date", "payment_type", "salary_month", "amount", "payment_method", "notes"], 60)
    return data


def loans_overview(params=None):
    loans = vehicle_filter(Loan.objects.filter(is_closed=False), params or {})
    return sum((ln.outstanding for ln in loans), ZERO)
