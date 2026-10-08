from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from bookings.models import Booking, Customer
from common.dates import today
from finance.models import DriverPayment, Expense, FuelEntry, Income, Loan, Maintenance
from fleet.models import Driver, Vehicle, VehicleType
from reports import services

D = Decimal


@override_settings(ALLOWED_HOSTS=["testserver"])
class Base(TestCase):
    def setUp(self):
        self.bus_type = VehicleType.objects.get(name="Tourist Bus")
        self.taxi_type = VehicleType.objects.get(name="Taxi")
        self.bus = Vehicle.objects.create(registration_number="ts09ab1234", short_name="Bus 1", vehicle_type=self.bus_type)
        self.taxi = Vehicle.objects.create(registration_number="TS08CD5678", short_name="Taxi 1", vehicle_type=self.taxi_type)
        self.customer = Customer.objects.create(name="Asha")
        self.t = today()

    def client_for(self, role):
        user = User.objects.create_user(username=role, password="Pass@12345", role=role)
        c = APIClient()
        c.force_authenticate(user)
        return c

    def booking(self, **kw):
        data = dict(customer=self.customer, vehicle=self.bus, trip_date=self.t, booking_amount=D("10000"))
        data.update(kw)
        return Booking.objects.create(**data)


class VehicleTypeTests(Base):
    def test_default_types_exist_and_new_types_can_be_added(self):
        names = set(VehicleType.objects.values_list("name", flat=True))
        self.assertTrue({"Tourist Bus", "Taxi", "Car", "Van / Traveller", "SUV", "Other"} <= names)
        tempo = VehicleType.objects.create(name="Tempo")
        v = Vehicle.objects.create(registration_number="X1", vehicle_type=tempo)
        Income.objects.create(vehicle=v, amount=D("500"))
        rows, _ = services.vehicle_breakdown(self.t.replace(day=1), self.t, {"vehicle_type": tempo.id})
        self.assertEqual(rows[0]["income"], D("500"))

    def test_registration_number_uppercased(self):
        self.assertEqual(self.bus.registration_number, "TS09AB1234")


class BookingTests(Base):
    def test_balance_is_amount_minus_advance(self):
        b = self.booking(advance=D("3000"))
        b = Booking.objects.with_totals().get(pk=b.pk)
        self.assertEqual(b.balance, D("7000"))
        self.assertEqual(b.payment_status, "partial")

    def test_advance_becomes_income_and_stays_in_sync(self):
        b = self.booking(advance=D("3000"))
        self.assertEqual(Income.objects.get(booking=b).amount, D("3000"))
        b.advance = D("4000")
        b.save()
        self.assertEqual(Income.objects.filter(booking=b).count(), 1)
        self.assertEqual(Income.objects.get(booking=b).amount, D("4000"))
        b.advance = D("0")
        b.save()
        self.assertFalse(Income.objects.filter(booking=b).exists())

    def test_final_payment_marks_paid(self):
        b = self.booking(advance=D("3000"))
        Income.objects.create(booking=b, amount=D("7000"))
        b = Booking.objects.with_totals().get(pk=b.pk)
        self.assertEqual(b.payment_status, "paid")
        self.assertEqual(b.balance, D("0"))
        # vehicle and customer filled from booking
        self.assertEqual(Income.objects.filter(vehicle=self.bus).count(), 2)

    def test_days_calculated(self):
        b = self.booking(return_date=self.t + timedelta(days=2))
        self.assertEqual(b.number_of_days, 3)

    def test_booking_numbers_increment(self):
        a, b = self.booking(), self.booking(trip_date=self.t + timedelta(days=5))
        self.assertNotEqual(a.booking_number, b.booking_number)
        self.assertTrue(b.booking_number.endswith("0002"))

    def test_ongoing_trip_sets_vehicle_on_trip_and_back(self):
        b = self.booking(trip_status="ongoing")
        self.bus.refresh_from_db()
        self.assertEqual(self.bus.status, "on_trip")
        b.trip_status = "completed"
        b.save()
        self.bus.refresh_from_db()
        self.assertEqual(self.bus.status, "available")

    def test_double_booking_blocked(self):
        c = self.client_for("manager")
        payload = {"customer": self.customer.id, "vehicle": self.bus.id, "trip_date": str(self.t),
                   "return_date": str(self.t + timedelta(days=2)), "booking_amount": 5000, "advance": 0}
        self.assertEqual(c.post("/api/bookings/", payload, format="json").status_code, 201)
        payload["trip_date"] = str(self.t + timedelta(days=1))
        r = c.post("/api/bookings/", payload, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("vehicle", r.data)

    def test_advance_more_than_amount_rejected(self):
        c = self.client_for("manager")
        r = c.post("/api/bookings/", {"customer": self.customer.id, "vehicle": self.bus.id, "trip_date": str(self.t),
                                      "booking_amount": 1000, "advance": 2000}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_delete_booking_removes_advance(self):
        b = self.booking(advance=D("1000"))
        b.delete()
        self.assertFalse(Income.objects.exists())


class ExpenseSourceTests(Base):
    def test_fuel_total_and_expense(self):
        f = FuelEntry.objects.create(vehicle=self.bus, litres=D("50.5"), rate=D("96.43"))
        self.assertEqual(f.total_amount, D("4869.72"))
        self.assertEqual(Expense.objects.get(fuel_entry=f).amount, D("4869.72"))
        f.litres = D("10")
        f.save()
        self.assertEqual(Expense.objects.get(fuel_entry=f).amount, D("964.30"))
        f.delete()
        self.assertFalse(Expense.objects.exists())

    def test_maintenance_total(self):
        m = Maintenance.objects.create(vehicle=self.bus, parts_cost=D("1200"), labour_cost=D("300.50"))
        self.assertEqual(m.total_cost, D("1500.50"))
        self.assertEqual(Expense.objects.get(maintenance=m).category, "maintenance")

    def test_linked_expense_cannot_be_edited_directly(self):
        f = FuelEntry.objects.create(vehicle=self.bus, litres=D("10"), rate=D("100"))
        c = self.client_for("accountant")
        r = c.delete(f"/api/expenses/{f.expense.id}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Fuel", r.data["detail"])

    def test_driver_salary_balance(self):
        drv = Driver.objects.create(name="Ravi", salary=D("20000"), assigned_vehicle=self.bus)
        DriverPayment.objects.create(driver=drv, payment_type="advance", salary_month=self.t, amount=D("5000"))
        DriverPayment.objects.create(driver=drv, payment_type="salary", salary_month=self.t, amount=D("10000"))
        DriverPayment.objects.create(driver=drv, payment_type="allowance", salary_month=self.t, amount=D("800"))
        st = services.salary_status(drv, self.t.replace(day=1))
        self.assertEqual(st["balance"], D("5000"))
        self.assertEqual(st["allowance"], D("800"))
        self.assertEqual(Expense.objects.filter(category="driver_salary", vehicle=self.bus).count(), 3)


class LoanTests(Base):
    def make_loan(self, **kw):
        data = dict(vehicle=self.bus, lender="HDFC", loan_amount=D("1000000"), interest_rate=D("10"),
                    start_date=date(2026, 1, 1), tenure_months=12, first_emi_date=date(2026, 1, 31))
        data.update(kw)
        return Loan.objects.create(**data)

    def test_emi_formula_and_schedule(self):
        loan = self.make_loan()
        self.assertEqual(loan.emi_amount, D("87915.89"))  # standard reducing-balance EMI
        insts = list(loan.installments.order_by("number"))
        self.assertEqual(len(insts), 12)
        self.assertEqual(insts[-1].closing_balance, D("0"))
        self.assertEqual(sum(i.principal for i in insts), D("1000000"))
        self.assertEqual(insts[1].due_date, date(2026, 2, 28))  # clamped to month end
        self.assertEqual(insts[2].due_date, date(2026, 3, 31))

    def test_zero_interest(self):
        loan = self.make_loan(interest_rate=D("0"), loan_amount=D("120000"))
        self.assertEqual(loan.emi_amount, D("10000.00"))

    def test_payment_reduces_outstanding_and_counts_as_emi(self):
        loan = self.make_loan()
        first = loan.installments.get(number=1)
        first.mark_paid(date(2026, 1, 31))
        loan.refresh_from_db()
        self.assertEqual(loan.outstanding, D("1000000") - first.principal)
        self.assertEqual(Expense.objects.get(installment=first).category, "emi")
        t = services.period_totals(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(t["emi"], first.amount)
        self.assertEqual(t["expenses"], D("0"))
        first.undo_payment()
        loan.refresh_from_db()
        self.assertEqual(loan.outstanding, D("1000000"))
        self.assertFalse(Expense.objects.exists())

    def test_terms_locked_after_payment(self):
        loan = self.make_loan()
        loan.installments.get(number=1).mark_paid()
        c = self.client_for("accountant")
        r = c.patch(f"/api/loans/{loan.id}/", {"tenure_months": 24}, format="json")
        self.assertEqual(r.status_code, 400)
        r = c.patch(f"/api/loans/{loan.id}/", {"notes": "ok"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_pay_endpoint(self):
        loan = self.make_loan()
        inst = loan.installments.get(number=1)
        c = self.client_for("accountant")
        r = c.post(f"/api/emi/{inst.id}/pay/", {"paid_date": "2026-02-02", "payment_method": "bank"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "paid")


class MonthlyFinanceTests(Base):
    def test_profit_formula_and_vehicle_split(self):
        start = self.t.replace(day=1)
        Income.objects.create(vehicle=self.bus, amount=D("50000"), date=start)
        Income.objects.create(vehicle=self.taxi, amount=D("8000"), date=start)
        FuelEntry.objects.create(vehicle=self.bus, litres=D("100"), rate=D("100"), date=start)  # 10,000
        Expense.objects.create(category="office", amount=D("3000"), date=start)  # no vehicle
        Expense.objects.create(category="emi", vehicle=self.bus, amount=D("20000"), date=start)
        data = services.monthly_finance(start, self.t.replace(day=28) if self.t.day < 28 else self.t, "", {})
        self.assertEqual(data["totals"]["income"], D("58000"))
        self.assertEqual(data["totals"]["expenses"], D("13000"))
        self.assertEqual(data["totals"]["emi"], D("20000"))
        self.assertEqual(data["totals"]["profit"], D("25000"))
        bus = next(r for r in data["vehicles"] if r["vehicle_id"] == self.bus.id)
        self.assertEqual((bus["income"], bus["expenses"], bus["emi"], bus["profit"]),
                         (D("50000"), D("10000"), D("20000"), D("20000")))
        self.assertEqual(data["general"]["expenses"], D("3000"))
        # vehicle rows + general row add up to the total
        total = sum(r["profit"] for r in data["vehicles"]) + data["general"]["profit"]
        self.assertEqual(total, data["totals"]["profit"])

    def test_vehicle_type_filter(self):
        Income.objects.create(vehicle=self.bus, amount=D("100"))
        Income.objects.create(vehicle=self.taxi, amount=D("40"))
        t = services.period_totals(self.t, self.t, {"vehicle_type": self.taxi_type.id})
        self.assertEqual(t["income"], D("40"))


class PermissionTests(Base):
    def test_role_access(self):
        staff, manager, accountant = (self.client_for(r) for r in ("staff", "manager", "accountant"))
        self.assertEqual(staff.get("/api/income/").status_code, 403)
        self.assertEqual(staff.get("/api/fuel/").status_code, 200)
        self.assertEqual(manager.get("/api/monthly-finance/").status_code, 403)
        self.assertEqual(manager.post("/api/customers/", {"name": "X"}).status_code, 201)
        self.assertEqual(accountant.post("/api/customers/", {"name": "X"}).status_code, 403)
        self.assertEqual(accountant.get("/api/reports/income/", {"export": "xlsx"}).status_code, 200)
        self.assertIsNone(manager.get("/api/dashboard/").data["finance"])
        self.assertIsNotNone(accountant.get("/api/dashboard/").data["finance"])

    def test_vehicle_with_records_cannot_be_deleted(self):
        Income.objects.create(vehicle=self.bus, amount=D("100"))
        r = self.client_for("admin").delete(f"/api/vehicles/{self.bus.id}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Sold", r.data["detail"])


class ReportTests(Base):
    def test_all_reports_and_exports(self):
        from reports.registry import REPORTS
        self.booking(advance=D("500"))
        FuelEntry.objects.create(vehicle=self.bus, litres=D("10"), rate=D("90"))
        c = self.client_for("admin")
        for key in REPORTS:
            for export in ("", "xlsx", "pdf"):
                r = c.get(f"/api/reports/{key}/", {"export": export})
                self.assertEqual(r.status_code, 200, f"{key} {export}")
