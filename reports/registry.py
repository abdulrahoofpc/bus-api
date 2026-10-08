"""
Report definitions. Each report returns:
  {title, subtitle, columns: [{key, label, type}], rows: [...], totals: {...}, summary: [...]}
The same result is shown on screen and exported to Excel / PDF.
Column types: text, date, money, number, status.
"""
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Min, Sum

from bookings.models import Booking
from bookings.views import customer_queryset
from common.dates import add_months, month_bounds, today
from common.money import money
from fleet.models import EXPIRY_LABELS, Driver, VehicleDocument
from finance.models import EMIInstallment, Expense, FuelEntry, Income, Loan, Maintenance
from finance.serializers import STATUS_LABELS

from . import services as svc

ZERO = Decimal("0")
REPORTS = OrderedDict()


def report(key, title, description, period="month", filters=("vehicle", "vehicle_type"), options=None):
    def wrap(fn):
        REPORTS[key] = {"key": key, "title": title, "description": description, "period": period,
                        "filters": list(filters), "options": options or [], "fn": fn}
        return fn
    return wrap


def col(key, label, type="text"):
    return {"key": key, "label": label, "type": type}


def sum_rows(rows, *keys):
    out = {}
    for k in keys:
        total = sum((r[k] or 0 for r in rows), 0)
        out[k] = total if isinstance(total, int) else money(total)
    return out


def by(qs, key, field="amount"):
    return sorted(svc.grouped(qs, key, field).items(), key=lambda x: -x[1])


def vlabel(v):
    return v.short_name or v.registration_number if v else ""


# ---------------------------------------------------------------------------
@report("income", "Income Report", "Every payment received, with totals by type.")
def income_report(p, start, end):
    qs = svc.incomes(start, end, p).select_related("vehicle", "customer", "booking").order_by("date", "id")
    if p.get("income_type"):
        qs = qs.filter(income_type=p["income_type"])
    rows = [{"date": i.date, "vehicle": vlabel(i.vehicle), "type": i.get_income_type_display(),
             "customer": i.customer.name if i.customer else "", "booking": i.booking.booking_number if i.booking else "",
             "method": i.get_payment_method_display(), "reference": i.reference, "amount": i.amount} for i in qs]
    by_type = by(qs, "income_type")
    names = dict(Income.IncomeType.choices)
    return {
        "columns": [col("date", "Date", "date"), col("vehicle", "Vehicle"), col("type", "Type"),
                    col("customer", "Customer"), col("booking", "Booking"), col("method", "Paid by"),
                    col("reference", "Reference"), col("amount", "Amount", "money")],
        "rows": rows, "totals": sum_rows(rows, "amount"),
        "summary": [{"label": names[k], "value": v} for k, v in by_type],
    }


@report("expense", "Expense Report", "Every expense including fuel, maintenance, salaries and EMI.")
def expense_report(p, start, end):
    qs = svc.expenses(start, end, p, include_emi=True).select_related("vehicle").order_by("date", "id")
    if p.get("category"):
        qs = qs.filter(category=p["category"])
    rows = [{"date": e.date, "vehicle": vlabel(e.vehicle) or "Office", "category": e.get_category_display(),
             "description": e.description, "method": e.get_payment_method_display(), "amount": e.amount} for e in qs]
    names = dict(Expense.Category.choices)
    by_cat = by(qs, "category")
    return {
        "columns": [col("date", "Date", "date"), col("vehicle", "Vehicle"), col("category", "Category"),
                    col("description", "Details"), col("method", "Paid by"), col("amount", "Amount", "money")],
        "rows": rows, "totals": sum_rows(rows, "amount"),
        "summary": [{"label": names[k], "value": v} for k, v in by_cat],
    }


@report("profit-loss", "Profit & Loss", "Income minus expenses and EMI for the period, by category.")
def profit_loss(p, start, end):
    rows = []
    inc_names, exp_names = dict(Income.IncomeType.choices), dict(Expense.Category.choices)
    inc = by(svc.incomes(start, end, p), "income_type")
    exp = by(svc.expenses(start, end, p), "category")
    t = svc.period_totals(start, end, p)
    rows.append({"particulars": "Income", "amount": None, "_style": "section"})
    rows += [{"particulars": f"    {inc_names[k]}", "amount": v} for k, v in inc]
    rows.append({"particulars": "Total income", "amount": t["income"], "_style": "subtotal"})
    rows.append({"particulars": "Expenses", "amount": None, "_style": "section"})
    rows += [{"particulars": f"    {exp_names[k]}", "amount": v} for k, v in exp]
    rows.append({"particulars": "Total expenses", "amount": t["expenses"], "_style": "subtotal"})
    rows.append({"particulars": "EMI paid", "amount": t["emi"], "_style": "subtotal"})
    return {
        "columns": [col("particulars", "Particulars"), col("amount", "Amount", "money")],
        "rows": rows,
        "totals": {"particulars": "Net profit" if t["profit"] >= 0 else "Net loss", "amount": t["profit"]},
        "summary": [{"label": "Income", "value": t["income"]}, {"label": "Expenses", "value": t["expenses"]},
                    {"label": "EMI", "value": t["emi"]}, {"label": "Net profit" if t["profit"] >= 0 else "Net loss", "value": t["profit"]}],
    }


@report("vehicle-profit", "Vehicle-wise Profit", "Income, expenses, EMI and profit for each vehicle.")
def vehicle_profit(p, start, end):
    rows, general = svc.vehicle_breakdown(start, end, p)
    rows = sorted(rows, key=lambda r: -r["profit"]) + ([general] if general else [])
    return {
        "columns": [col("vehicle", "Vehicle"), col("registration_number", "Reg. No."), col("vehicle_type", "Type"),
                    col("trips", "Trips", "number"), col("income", "Income", "money"),
                    col("expenses", "Expenses", "money"), col("emi", "EMI", "money"), col("profit", "Profit", "money")],
        "rows": rows, "totals": sum_rows(rows, "trips", "income", "expenses", "emi", "profit"),
    }


@report("trip-profit", "Trip-wise Profit", "Booking amount against fuel, toll and other trip costs.",
        options=[{"key": "trip_status", "label": "Trip status",
                  "choices": [["", "All trips"]] + [list(c) for c in Booking.TripStatus.choices]}])
def trip_profit(p, start, end):
    qs = svc.vehicle_filter(Booking.objects.with_totals().filter(trip_date__range=(start, end)), p)
    qs = qs.filter(trip_status=p["trip_status"]) if p.get("trip_status") else qs.exclude(trip_status="cancelled")
    costs = svc.grouped(Expense.objects.filter(booking__in=qs).exclude(category="emi"), "booking")
    rows = []
    for b in qs.select_related("customer", "vehicle").order_by("trip_date"):
        c = costs.get(b.id, ZERO)
        rows.append({"booking": b.booking_number, "date": b.trip_date, "customer": b.customer.name,
                     "vehicle": vlabel(b.vehicle), "route": " to ".join(x for x in [b.pickup_location, b.destination] if x),
                     "amount": b.booking_amount, "received": money(b.received), "balance": b.balance,
                     "costs": c, "profit": b.booking_amount - c})
    return {
        "columns": [col("booking", "Booking"), col("date", "Trip date", "date"), col("customer", "Customer"),
                    col("vehicle", "Vehicle"), col("route", "Route"), col("amount", "Amount", "money"),
                    col("received", "Received", "money"), col("balance", "Balance", "money"),
                    col("costs", "Trip costs", "money"), col("profit", "Profit", "money")],
        "rows": rows, "totals": sum_rows(rows, "amount", "received", "balance", "costs", "profit"),
    }


@report("fuel", "Fuel Report", "Fuel filled, grouped by entry, vehicle, month or trip.",
        options=[{"key": "group", "label": "Show", "choices": [["entries", "Every entry"], ["vehicle", "By vehicle"],
                                                               ["month", "By month"], ["trip", "By trip"]]}])
def fuel_report(p, start, end):
    qs = svc.vehicle_filter(FuelEntry.objects.filter(date__range=(start, end)), p).select_related("vehicle", "driver", "booking")
    group = p.get("group") or "entries"
    if group == "vehicle":
        rows = []
        for v in (qs.values("vehicle", "vehicle__short_name", "vehicle__registration_number")
                  .annotate(litres=Sum("litres"), amount=Sum("total_amount"), fills=Count("id"),
                            odo_min=Min("odometer"), odo_max=Max("odometer"))
                  .order_by("-amount")):
            km = (v["odo_max"] - v["odo_min"]) if v["odo_min"] and v["odo_max"] else None
            # Full-tank method: the first fill's fuel was used before the first reading, so leave it out.
            first = qs.filter(vehicle=v["vehicle"], odometer__isnull=False).order_by("odometer").first()
            used = float(v["litres"]) - float(first.litres) if first else 0
            rows.append({"vehicle": v["vehicle__short_name"] or v["vehicle__registration_number"],
                         "fills": v["fills"], "litres": money(v["litres"]), "amount": money(v["amount"]),
                         "avg_rate": (v["amount"] / v["litres"]).quantize(Decimal("0.01")) if v["litres"] else ZERO,
                         "km": km or "", "kmpl": round(km / used, 2) if km and used > 0 else ""})
        return {"columns": [col("vehicle", "Vehicle"), col("fills", "Fills", "number"), col("litres", "Litres", "number"),
                            col("avg_rate", "Avg rate", "money"), col("amount", "Amount", "money"),
                            col("km", "Km driven*", "number"), col("kmpl", "Km / litre*", "number")],
                "rows": rows, "totals": sum_rows(rows, "fills", "litres", "amount"),
                "note": "* Approximate, based on the first and last odometer readings in the period (fill the tank fully each time for best accuracy)."}
    if group == "month":
        buckets = OrderedDict()
        for f in qs.order_by("date"):
            b = buckets.setdefault(f.date.strftime("%b %Y"), {"month": f.date.strftime("%b %Y"), "fills": 0,
                                                              "litres": ZERO, "amount": ZERO})
            b["fills"] += 1
            b["litres"] += f.litres
            b["amount"] += f.total_amount
        rows = list(buckets.values())
        return {"columns": [col("month", "Month"), col("fills", "Fills", "number"), col("litres", "Litres", "number"),
                            col("amount", "Amount", "money")], "rows": rows,
                "totals": sum_rows(rows, "fills", "litres", "amount")}
    if group == "trip":
        rows = [{"booking": r["booking__booking_number"] or "Not linked to a trip",
                 "customer": r["booking__customer__name"] or "", "litres": money(r["litres"]), "amount": money(r["amount"])}
                for r in qs.values("booking__booking_number", "booking__customer__name")
                .annotate(litres=Sum("litres"), amount=Sum("total_amount")).order_by("-amount")]
        return {"columns": [col("booking", "Trip"), col("customer", "Customer"), col("litres", "Litres", "number"),
                            col("amount", "Amount", "money")], "rows": rows, "totals": sum_rows(rows, "litres", "amount")}
    rows = [{"date": f.date, "vehicle": vlabel(f.vehicle), "driver": f.driver.name if f.driver else "",
             "trip": f.booking.booking_number if f.booking else "", "litres": f.litres, "rate": f.rate,
             "amount": f.total_amount, "odometer": f.odometer or "", "station": f.fuel_station}
            for f in qs.order_by("date", "id")]
    return {"columns": [col("date", "Date", "date"), col("vehicle", "Vehicle"), col("driver", "Driver"),
                        col("trip", "Trip"), col("litres", "Litres", "number"), col("rate", "Rate", "money"),
                        col("amount", "Amount", "money"), col("odometer", "Odometer", "number"),
                        col("station", "Fuel station")],
            "rows": rows, "totals": sum_rows(rows, "litres", "amount")}


@report("maintenance", "Maintenance Report", "Service and repair work with parts and labour costs.")
def maintenance_report(p, start, end):
    qs = svc.vehicle_filter(Maintenance.objects.filter(date__range=(start, end), status="completed"), p).select_related("vehicle")
    rows = [{"date": m.date, "vehicle": vlabel(m.vehicle), "type": m.get_maintenance_type_display(),
             "workshop": m.workshop, "description": m.description, "parts": m.parts_cost, "labour": m.labour_cost,
             "total": m.total_cost, "next": m.next_service_date} for m in qs.order_by("date", "id")]
    return {"columns": [col("date", "Date", "date"), col("vehicle", "Vehicle"), col("type", "Type"),
                        col("workshop", "Workshop"), col("description", "Work done"), col("parts", "Parts", "money"),
                        col("labour", "Labour", "money"), col("total", "Total", "money"),
                        col("next", "Next service", "date")],
            "rows": rows, "totals": sum_rows(rows, "parts", "labour", "total")}


@report("emi", "EMI Report", "EMIs due in the period with their payment status, plus loan balances.",
        options=[{"key": "status", "label": "Status", "choices": [["", "All"], ["paid", "Paid"],
                                                                  ["unpaid", "Pending"], ["overdue", "Overdue"]]}])
def emi_report(p, start, end):
    qs = svc.vehicle_filter(EMIInstallment.objects.filter(due_date__range=(start, end)), p, path="loan__vehicle")
    t = today()
    if p.get("status") == "paid":
        qs = qs.filter(paid_date__isnull=False)
    elif p.get("status") == "unpaid":
        qs = qs.filter(paid_date__isnull=True)
    elif p.get("status") == "overdue":
        qs = qs.filter(paid_date__isnull=True, due_date__lt=t)
    rows = [{"due": i.due_date, "vehicle": vlabel(i.loan.vehicle), "lender": i.loan.lender,
             "emi": f"{i.number} of {i.loan.tenure_months}", "amount": i.amount, "principal": i.principal,
             "interest": i.interest, "status": STATUS_LABELS[i.status], "_status": i.status,
             "paid_on": i.paid_date, "paid": i.paid_amount or ZERO}
            for i in qs.select_related("loan", "loan__vehicle").order_by("due_date")]
    loans = svc.vehicle_filter(Loan.objects.filter(is_closed=False), p)
    all_unpaid = svc.vehicle_filter(EMIInstallment.objects.filter(paid_date__isnull=True), p, path="loan__vehicle")
    return {"columns": [col("due", "Due date", "date"), col("vehicle", "Vehicle"), col("lender", "Lender"),
                        col("emi", "EMI no."), col("amount", "EMI", "money"), col("principal", "Principal", "money"),
                        col("interest", "Interest", "money"), col("status", "Status", "status"),
                        col("paid_on", "Paid on", "date"), col("paid", "Paid", "money")],
            "rows": rows, "totals": sum_rows(rows, "amount", "principal", "interest", "paid"),
            "summary": [
                {"label": "Paid in period", "value": sum((r["paid"] for r in rows), ZERO)},
                {"label": "Pending in period", "value": sum((r["amount"] for r in rows if r["_status"] != "paid"), ZERO)},
                {"label": "Overdue (all time)", "value": all_unpaid.filter(due_date__lt=t).aggregate(s=Sum("amount"))["s"] or ZERO},
                {"label": "Total loan outstanding", "value": sum((ln.outstanding for ln in loans), ZERO)},
            ]}


@report("driver-salary", "Driver Salary Report", "Salary due, paid, advances and allowances per driver.",
        filters=("vehicle",))
def driver_salary(p, start, end):
    months = []
    m = start.replace(day=1)
    while m <= end:
        months.append(m)
        m = add_months(m, 1)
    drivers = Driver.objects.select_related("assigned_vehicle")
    if p.get("vehicle"):
        drivers = drivers.filter(assigned_vehicle_id=p["vehicle"])
    rows = []
    for d in drivers:
        pays = d.payments.filter(salary_month__in=months)
        if d.status != "active" and not pays.exists():
            continue
        by_type = svc.grouped(pays, "payment_type")
        due = d.salary * len(months)
        paid, adv, allow = by_type.get("salary", ZERO), by_type.get("advance", ZERO), by_type.get("allowance", ZERO)
        rows.append({"driver": d.name, "vehicle": vlabel(d.assigned_vehicle), "salary": due, "paid": paid,
                     "advance": adv, "allowance": allow, "balance": max(due - paid - adv, ZERO)})
    return {"columns": [col("driver", "Driver"), col("vehicle", "Vehicle"), col("salary", "Salary due", "money"),
                        col("paid", "Salary paid", "money"), col("advance", "Advances", "money"),
                        col("allowance", "Allowance", "money"), col("balance", "Balance to pay", "money")],
            "rows": rows, "totals": sum_rows(rows, "salary", "paid", "advance", "allowance", "balance")}


@report("customer-outstanding", "Customer Outstanding", "Customers who still owe money on their bookings.",
        period=None, filters=())
def customer_outstanding(p, start, end):
    rows = [{"customer": c.name, "company": c.company_name, "phone": c.phone, "bookings": c.total_bookings,
             "total": c.total_amount, "paid": c.paid_amount, "pending": c.pending_amount}
            for c in customer_queryset().filter(pending_amount__gt=0).order_by("-pending_amount")]
    return {"columns": [col("customer", "Customer"), col("company", "Company"), col("phone", "Phone"),
                        col("bookings", "Bookings", "number"), col("total", "Total billed", "money"),
                        col("paid", "Paid", "money"), col("pending", "Pending", "money")],
            "rows": rows, "totals": sum_rows(rows, "bookings", "total", "paid", "pending")}


@report("document-expiry", "Document Expiry", "Insurance, permit, fitness, tax and pollution expiry dates.",
        period=None,
        options=[{"key": "status", "label": "Show", "choices": [["attention", "Expired or due in 30 days"],
                                                                ["", "All documents"]]}])
def document_expiry(p, start, end):
    t = today()
    qs = svc.vehicle_filter(VehicleDocument.objects.select_related("vehicle"), p)
    include_licences = not p.get("vehicle") and not p.get("vehicle_type")
    drivers = Driver.objects.filter(status="active", licence_expiry__isnull=False)
    if p.get("status", "attention") == "attention":
        qs = qs.filter(expiry_date__lte=t + timedelta(days=30))
        drivers = drivers.filter(licence_expiry__lte=t + timedelta(days=30))
    rows = [{"vehicle": vlabel(d.vehicle), "document": d.get_doc_type_display(), "number": d.document_number,
             "provider": d.provider, "expiry": d.expiry_date, "days": d.days_left,
             "status": EXPIRY_LABELS[d.status], "_status": d.status} for d in qs]
    if include_licences:
        rows += [{"vehicle": f"Driver: {d.name}", "document": "Driving licence", "number": d.licence_number,
                  "provider": "", "expiry": d.licence_expiry, "days": (d.licence_expiry - t).days,
                  "status": EXPIRY_LABELS[d.licence_status], "_status": d.licence_status} for d in drivers]
    rows.sort(key=lambda r: r["expiry"])
    return {"columns": [col("vehicle", "Vehicle / driver"), col("document", "Document"), col("number", "Number"),
                        col("provider", "Issued by"), col("expiry", "Expiry date", "date"),
                        col("days", "Days left", "number"), col("status", "Status", "status")],
            "rows": rows, "totals": {}}


@report("yearly-summary", "Yearly Summary", "Month-by-month income, expenses, EMI and profit for a year.",
        period="year")
def yearly_summary(p, start, end):
    rows = []
    for m in range(1, 13):
        ms, me = month_bounds(start.year, m)
        t = svc.period_totals(ms, me, p)
        trips = svc.vehicle_filter(Booking.objects.filter(trip_date__range=(ms, me)).exclude(trip_status="cancelled"), p).count()
        rows.append({"month": ms.strftime("%B"), "trips": trips, **t})
    return {"columns": [col("month", "Month"), col("trips", "Trips", "number"), col("income", "Income", "money"),
                        col("expenses", "Expenses", "money"), col("emi", "EMI", "money"), col("profit", "Profit", "money")],
            "rows": rows, "totals": sum_rows(rows, "trips", "income", "expenses", "emi", "profit")}


# ---------------------------------------------------------------------------
@report("driver-monthly", "Monthly Driver Report",
        "Trips, kilometres, fuel and expenses per driver. Pending expenses are shown but not counted as spent.",
        filters=("vehicle", "vehicle_type", "driver"))
def driver_monthly(p, start, end):
    drivers = Driver.objects.all()
    if p.get("driver"):
        drivers = drivers.filter(pk=p["driver"])
    rows = []
    for d in drivers:
        trips = Booking.objects.filter(driver=d, trip_date__range=(start, end))
        fuel = FuelEntry.objects.filter(driver=d, date__range=(start, end))
        exp = Expense.all_objects.filter(driver=d, date__range=(start, end))
        maint = Maintenance.objects.filter(driver=d, date__range=(start, end))
        if p.get("vehicle"):
            trips, fuel, exp, maint = (q.filter(vehicle_id=p["vehicle"]) for q in (trips, fuel, exp, maint))
        if p.get("vehicle_type"):
            trips, fuel, exp, maint = (q.filter(vehicle__vehicle_type_id=p["vehicle_type"]) for q in (trips, fuel, exp, maint))
        km = sum((b.total_km or 0) for b in trips.filter(trip_status="completed"))
        f = fuel.aggregate(l=Sum("litres"), a=Sum("total_amount"))
        row = {
            "driver": d.name, "trips": trips.exclude(trip_status="cancelled").count(),
            "completed": trips.filter(trip_status="completed").count(), "km": km,
            "litres": f["l"] or ZERO, "fuel_cost": money(f["a"] or 0),
            "approved": money(exp.filter(status="approved").aggregate(t=Sum("amount"))["t"] or 0),
            "pending": money(exp.filter(status="pending").aggregate(t=Sum("amount"))["t"] or 0),
            "reports": maint.count(),
        }
        if any(row[k] for k in ("trips", "litres", "approved", "pending", "reports")) or p.get("driver"):
            rows.append(row)
    return {
        "columns": [col("driver", "Driver"), col("trips", "Trips", "number"), col("completed", "Completed", "number"),
                    col("km", "Total KM", "number"), col("litres", "Fuel litres", "number"),
                    col("fuel_cost", "Fuel cost", "money"), col("approved", "Approved expenses", "money"),
                    col("pending", "Pending expenses", "money"), col("reports", "Maintenance reports", "number")],
        "rows": rows,
        "totals": sum_rows(rows, "trips", "completed", "km", "litres", "fuel_cost", "approved", "pending", "reports"),
        "summary": [],
        "note": "Total KM comes from the start and end odometer readings drivers enter for completed trips.",
    }


@report("vehicle-monthly", "Monthly Vehicle Report",
        "Trips, kilometres and the full cost picture for each vehicle. Only approved records are counted.",
        filters=("vehicle", "vehicle_type", "driver"))
def vehicle_monthly(p, start, end):
    from fleet.models import Vehicle
    vehicles = Vehicle.objects.select_related("vehicle_type")
    if p.get("vehicle"):
        vehicles = vehicles.filter(pk=p["vehicle"])
    if p.get("vehicle_type"):
        vehicles = vehicles.filter(vehicle_type_id=p["vehicle_type"])
    rows = []
    for v in vehicles:
        trips = Booking.objects.filter(vehicle=v, trip_date__range=(start, end)).exclude(trip_status="cancelled")
        inc = Income.objects.filter(vehicle=v, date__range=(start, end))
        exp = Expense.objects.filter(vehicle=v, date__range=(start, end))   # approved only
        if p.get("driver"):
            trips = trips.filter(driver_id=p["driver"])
            inc = inc.filter(booking__driver_id=p["driver"])
            exp = exp.filter(booking__driver_id=p["driver"])
        amount = lambda q: money(q.aggregate(t=Sum("amount"))["t"] or 0)  # noqa: E731
        income = amount(inc)
        fuel = amount(exp.filter(category="fuel"))
        maint = amount(exp.filter(category="maintenance"))
        emi = amount(exp.filter(category="emi"))
        salary = amount(exp.filter(category="driver_salary"))
        other = amount(exp.exclude(category__in=["fuel", "maintenance", "emi", "driver_salary"]))
        row = {"vehicle": vlabel(v), "type": v.vehicle_type.name, "trips": trips.count(),
               "km": sum((b.total_km or 0) for b in trips.filter(trip_status="completed")),
               "income": income, "fuel": fuel, "other": other, "maintenance": maint, "salary": salary, "emi": emi,
               "profit": money(income - fuel - other - maint - salary - emi)}
        if any(row[k] for k in ("trips", "income", "fuel", "other", "maintenance", "salary", "emi")) or p.get("vehicle"):
            rows.append(row)
    return {
        "columns": [col("vehicle", "Vehicle"), col("type", "Type"), col("trips", "Trips", "number"),
                    col("km", "Total KM", "number"), col("income", "Trip income", "money"), col("fuel", "Fuel", "money"),
                    col("other", "Other expenses", "money"), col("maintenance", "Maintenance", "money"),
                    col("salary", "Driver costs", "money"), col("emi", "EMI", "money"), col("profit", "Net profit", "money")],
        "rows": rows,
        "totals": sum_rows(rows, "trips", "km", "income", "fuel", "other", "maintenance", "salary", "emi", "profit"),
        "summary": [],
        "note": "Net profit = Trip income − Fuel − Other expenses − Maintenance − Driver costs − EMI.",
    }


def run(key, params):
    from common.dates import resolve_period
    from fleet.models import Vehicle, VehicleType

    meta = REPORTS[key]
    params = {k: v for k, v in params.items() if v not in (None, "")}
    if meta["period"] == "year":
        year = int(params.get("year") or today().year)
        start, end, label = date(year, 1, 1), date(year, 12, 31), f"Year {year}"
    elif meta["period"] is None:
        start, end, label = date(1990, 1, 1), date(2100, 12, 31), f"As on {today():%d %b %Y}"
    else:
        start, end, label = resolve_period(params)
    result = meta["fn"](params, start, end)
    filters = []
    if params.get("vehicle"):
        v = Vehicle.objects.filter(pk=params["vehicle"]).first()
        filters.append(f"Vehicle: {v.label if v else '-'}")
    if params.get("vehicle_type"):
        vt = VehicleType.objects.filter(pk=params["vehicle_type"]).first()
        filters.append(f"Type: {vt.name if vt else '-'}")
    if params.get("driver"):
        d = Driver.objects.filter(pk=params["driver"]).first()
        filters.append(f"Driver: {d.name if d else '-'}")
    result.update({"key": key, "title": meta["title"], "period": label,
                   "filters_applied": filters, "generated": today()})
    result.setdefault("summary", [])
    return result


def catalogue():
    return [{k: v for k, v in r.items() if k != "fn"} for r in REPORTS.values()]
