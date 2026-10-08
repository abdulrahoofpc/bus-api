"""
Business and security rules, checked through the REST API only (the same calls the app makes).
Ported from the old web-page tests: approvals, maintenance cost timing, fuel → finance,
upload checks, driver privacy, logins, reports/exports and every list endpoint.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from bookings.models import Booking, Customer
from finance.models import Expense, FuelEntry, Maintenance
from fleet.models import Driver, Vehicle, VehicleType
from portal.models import DriverActivity, Notification
from reports import registry, services


def jpg(name="bill.jpg"):
    return SimpleUploadedFile(name, b"\xff\xd8\xff" + b"0" * 100, content_type="image/jpeg")


@override_settings(MEDIA_ROOT="/tmp/fleetbooks-test-media")
class ApiRuleTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("boss", "boss@x.in", "Pass@12345")
        User.objects.create_user("acc", password="Pass@12345", role="accountant")
        vt = VehicleType.objects.get_or_create(name="Tourist Bus")[0]
        self.bus = Vehicle.objects.create(registration_number="KL07AB1234", vehicle_type=vt)
        self.bus2 = Vehicle.objects.create(registration_number="KL07AB5678", vehicle_type=vt)
        self.customer = Customer.objects.create(name="ABC Travels")
        self.today = timezone.localdate()
        self.office = self.client_for("boss", "Pass@12345")

    # ------------------------------------------------------------ helpers
    def client_for(self, username, password):
        c = APIClient()
        r = c.post("/api/auth/login/", {"username": username, "password": password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        c.token = r.data["access"]
        return c

    def make_driver(self, name="John Mathew", username="9876543210", vehicle=None):
        r = self.office.post("/api/office/r/drivers/", {"name": name, "phone": username, "status": "active", "salary": "18000",
                                                        "assigned_vehicle": (vehicle or self.bus).pk, "login_username": username,
                                                        "login_password": "Drive@2026"}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        return Driver.objects.get(name=name)

    def trip(self, driver, **kw):
        return Booking.objects.create(customer=self.customer, vehicle=kw.pop("vehicle", self.bus), driver=driver,
                                      trip_date=kw.pop("trip_date", self.today), booking_amount=Decimal("30000"),
                                      pickup_location="Kochi", destination="Munnar", **kw)

    # ------------------------------------------------------------ logins
    def test_deactivated_driver_cannot_log_in_and_reactivation_restores_it(self):
        d = self.make_driver()
        self.office.post(f"/api/office/drivers/{d.pk}/account/", {"action": "deactivate"}, format="json")
        r = APIClient().post("/api/auth/login/", {"username": "9876543210", "password": "Drive@2026"}, format="json")
        self.assertEqual(r.status_code, 401)
        self.office.post(f"/api/office/drivers/{d.pk}/account/", {"action": "activate"}, format="json")
        self.client_for("9876543210", "Drive@2026")

    def test_drivers_are_refused_by_every_office_api(self):
        self.make_driver()
        drv = self.client_for("9876543210", "Drive@2026")
        for url in ["/api/office/dashboard/", "/api/office/resources/", "/api/office/r/bookings/", "/api/office/expense-approvals/",
                    "/api/office/activity/", "/api/income/", "/api/dashboard/", "/api/drivers/", "/api/expenses/",
                    "/api/reports/", "/api/reports/profit-loss/", "/api/monthly-finance/", "/api/auth/users/"]:
            self.assertIn(drv.get(url).status_code, (401, 403), url)

    # ------------------------------------------------------------ trip flow
    def test_full_trip_flow_with_odometer_duration_and_notifications(self):
        d = self.make_driver()
        t = self.trip(d)
        drv = self.client_for("9876543210", "Drive@2026")
        self.assertEqual(drv.post(f"/api/driver/trips/{t.pk}/accept/").status_code, 200)
        start = timezone.localtime().replace(second=0, microsecond=0) - timedelta(hours=5)
        r = drv.post(f"/api/driver/trips/{t.pk}/start/", {"start_odometer": 45200, "start_location": "Kochi",
                                                          "started_at": start.strftime("%Y-%m-%dT%H:%M")}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.bus.refresh_from_db(); self.assertEqual(self.bus.status, "on_trip")
        r = drv.post(f"/api/driver/trips/{t.pk}/complete/", {"end_odometer": 45000, "end_location": "Munnar"}, format="json")
        self.assertEqual(r.status_code, 400)                                 # lower than the starting reading
        r = drv.post(f"/api/driver/trips/{t.pk}/complete/", {
            "end_odometer": 45330, "end_location": "Munnar",
            "completed_at": (start + timedelta(hours=4, minutes=30)).strftime("%Y-%m-%dT%H:%M"),
            "trip_notes": "Smooth", "extra_category": "toll", "extra_amount": "250"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        t.refresh_from_db(); self.bus.refresh_from_db()
        self.assertEqual((t.trip_status, t.total_km, t.duration_text), ("completed", 130, "4 h 30 min"))
        self.assertEqual(self.bus.status, "available")
        self.assertEqual(Expense.all_objects.get(booking=t).status, "pending")
        actions = set(DriverActivity.objects.filter(driver=d).values_list("action", flat=True))
        self.assertTrue({"trip_accepted", "trip_started", "trip_completed", "expense_submitted"} <= actions)
        n = Notification.objects.filter(user=self.boss, text__contains="completed trip").first()
        self.assertEqual(n.url, f"/bookings/?search={t.booking_number}")    # path the app maps to a screen

    # ------------------------------------------------------------ money rules
    def test_driver_fuel_goes_straight_to_vehicle_trip_and_monthly_finance(self):
        d = self.make_driver()
        t = self.trip(d)
        drv = self.client_for("9876543210", "Drive@2026")
        r = drv.post("/api/driver/fuel/", {"vehicle": self.bus.pk, "booking": t.pk, "date": self.today.isoformat(),
                                           "fuel_type": "diesel", "litres": "45", "rate": "100", "payment_method": "cash",
                                           "receipt": jpg()}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        f = FuelEntry.objects.get(driver=d)
        self.assertEqual(f.total_amount, Decimal("4500.00"))                 # Litres × Rate
        self.assertEqual(services.period_totals(self.today.replace(day=1), self.today)["expenses"], Decimal("4500.00"))
        self.assertEqual(services.trip_costs(t), Decimal("4500.00"))

    def test_bad_upload_is_rejected(self):
        self.make_driver()
        drv = self.client_for("9876543210", "Drive@2026")
        bad = SimpleUploadedFile("virus.exe", b"MZ....", content_type="application/octet-stream")
        r = drv.post("/api/driver/expenses/", {"vehicle": self.bus.pk, "date": self.today.isoformat(), "category": "toll",
                                               "amount": "100", "payment_method": "cash", "receipt": bad}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertIn("receipt", r.data["errors"])
        self.assertFalse(Expense.all_objects.exists())

    def test_expense_pending_then_approved_counts_rejected_never_counts(self):
        d = self.make_driver()
        t = self.trip(d)
        drv = self.client_for("9876543210", "Drive@2026")
        for amt in ("850", "600"):
            r = drv.post("/api/driver/expenses/", {"vehicle": self.bus.pk, "booking": t.pk, "date": self.today.isoformat(),
                                                   "category": "toll", "amount": amt, "payment_method": "cash"}, format="multipart")
            self.assertEqual(r.data["status"], "pending")
        e2, e1 = Expense.all_objects.filter(driver=d).order_by("amount")
        start, end = self.today.replace(day=1), self.today
        self.assertEqual(services.period_totals(start, end)["expenses"], 0)            # pending doesn't count
        self.assertEqual(drv.post(f"/api/office/expenses/{e1.pk}/decide/", {"action": "approve"}, format="json").status_code, 403)
        self.assertEqual(self.office.post(f"/api/office/expenses/{e2.pk}/decide/", {"action": "reject"}, format="json").status_code, 400)
        self.office.post(f"/api/office/expenses/{e2.pk}/decide/", {"action": "reject", "reason": "No receipt"}, format="json")
        self.office.post(f"/api/office/expenses/{e1.pk}/decide/", {"action": "approve"}, format="json")
        e1.refresh_from_db(); e2.refresh_from_db()
        self.assertEqual((e1.status, e2.status), ("approved", "rejected"))
        self.assertEqual(services.period_totals(start, end)["expenses"], Decimal("850.00"))   # approved only
        self.assertEqual(services.trip_costs(t), Decimal("850.00"))
        vrow = next(r for r in registry.run("vehicle-monthly", {"month": self.today.month, "year": self.today.year})["rows"]
                    if r["vehicle"] == "KL07AB1234")
        self.assertEqual(vrow["other"], Decimal("850.00"))
        self.assertTrue(Notification.objects.filter(user=d.user, text__contains="rejected: No receipt").exists())
        self.assertEqual(drv.post(f"/api/driver/expenses/{e1.pk}/withdraw/").status_code, 409)   # reviewed: can't withdraw
        self.assertTrue(Expense.all_objects.filter(pk=e1.pk).exists())

    def test_problem_report_costs_count_only_when_completed(self):
        d = self.make_driver()
        drv = self.client_for("9876543210", "Drive@2026")
        photo = SimpleUploadedFile("tyre.png", b"\x89PNG" + b"0" * 50, content_type="image/png")
        r = drv.post("/api/driver/maintenance/", {"vehicle": self.bus.pk, "date": self.today.isoformat(), "problem_category": "tyre",
                                                  "priority": "high", "description": "Front left tyre worn", "photo": photo},
                     format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        m = Maintenance.objects.get(driver=d)
        self.assertEqual((m.status, m.maintenance_type), ("reported", "tyres"))
        base = {"vehicle": self.bus.pk, "date": self.today.isoformat(), "maintenance_type": "tyres", "payment_method": "cash",
                "priority": "high", "problem_category": "tyre", "parts_cost": "8000", "labour_cost": "500"}
        r = self.office.post(f"/api/office/r/maintenance/{m.pk}/", {**base, "status": "in_progress"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Expense.all_objects.filter(maintenance=m).exists())            # not finished → no cost yet
        self.office.post(f"/api/office/r/maintenance/{m.pk}/", {**base, "status": "completed"}, format="multipart")
        self.assertEqual(Expense.objects.get(maintenance=m).amount, Decimal("8500.00"))  # Parts + Labour
        self.assertTrue(Notification.objects.filter(user=d.user, text__contains="Completed").exists())

    # ------------------------------------------------------------ privacy
    def test_driver_cannot_touch_another_drivers_records(self):
        self.make_driver()
        tom = self.make_driver("Thomas Joseph", "9123456780", vehicle=self.bus2)
        toms_trip = self.trip(tom, vehicle=self.bus2)
        toms_expense = Expense.all_objects.create(status="pending", driver=tom, vehicle=self.bus2, category="toll",
                                                  amount=100, receipt=jpg("t.jpg"))
        drv = self.client_for("9876543210", "Drive@2026")
        self.assertEqual(drv.get(f"/api/driver/trips/{toms_trip.pk}/").status_code, 404)
        self.assertEqual(drv.post(f"/api/driver/trips/{toms_trip.pk}/accept/").status_code, 404)
        self.assertEqual(drv.post(f"/api/driver/trips/{toms_trip.pk}/start/", {"start_odometer": 1}, format="json").status_code, 404)
        self.assertEqual(drv.post(f"/api/driver/expenses/{toms_expense.pk}/withdraw/").status_code, 404)
        self.assertEqual(drv.get(f"/api/driver/expenses/{toms_expense.pk}/").status_code, 404)
        self.assertEqual(APIClient().get(f"{toms_expense.receipt.url}?token={drv.token}").status_code, 404)
        r = drv.post("/api/driver/fuel/", {"vehicle": self.bus2.pk, "booking": toms_trip.pk, "date": self.today.isoformat(),
                                           "fuel_type": "diesel", "litres": "10", "rate": "100", "payment_method": "cash"},
                     format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(FuelEntry.objects.exists())
        body = drv.get("/api/driver/dashboard/").content.decode() + drv.get("/api/driver/trips/?show=all").content.decode()
        self.assertNotIn("30000", body)                                             # booking amounts never sent to drivers
        self.assertNotIn("profit", body.lower())

    # ------------------------------------------------------------ office screens
    def test_every_office_list_and_report_endpoint_answers(self):
        d = self.make_driver()
        self.trip(d)
        defs = self.office.get("/api/office/resources/").json()
        for key in defs:
            self.assertEqual(self.office.get(f"/api/office/r/{key}/", {"mode": "all"}).status_code, 200, key)
        for url in ["/api/office/dashboard/", "/api/office/expense-approvals/", "/api/office/activity/",
                    f"/api/office/drivers/{d.pk}/overview/", "/api/office/assign-trip/", "/api/office/notifications/unread/",
                    "/api/monthly-finance/", "/api/reports/", f"/api/vehicles/{self.bus.pk}/profile/", "/api/emi/",
                    "/api/auth/me/", "/api/auth/business/"]:
            self.assertEqual(self.office.get(url).status_code, 200, url)
        dash = self.office.get("/api/office/dashboard/").json()
        self.assertIn("board", dash); self.assertIn("attention", dash); self.assertIn("drivers", dash)
        for item in dash["attention"]:
            self.assertTrue(item["url"].startswith("/"), item)
        for report in self.office.get("/api/reports/").json():
            key = report["key"] if isinstance(report, dict) else report
            self.assertEqual(self.office.get(f"/api/reports/{key}/").status_code, 200, key)
        for key in ("driver-monthly", "vehicle-monthly", "profit-loss"):
            pdf = self.office.get(f"/api/reports/{key}/", {"export": "pdf"})
            xlsx = self.office.get(f"/api/reports/{key}/", {"export": "xlsx"})
            self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"), key)
            self.assertEqual(xlsx.status_code, 200, key)
        acc = self.client_for("acc", "Pass@12345")
        self.assertEqual(acc.get("/api/monthly-finance/").status_code, 200)
        self.assertEqual(acc.post(f"/api/office/drivers/{d.pk}/account/", {"action": "deactivate"}, format="json").status_code, 403)

    def test_office_records_edit_with_patch_put_and_json(self):
        c = self.customer
        r = self.office.patch(f"/api/office/r/customers/{c.pk}/", {"notes": "VIP"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        self.assertEqual((c.notes, c.name), ("VIP", "ABC Travels"))          # only the field sent changes
        self.office.patch(f"/api/office/r/customers/{c.pk}/", {"phone": "9847000000"}, format="multipart")
        c.refresh_from_db(); self.assertEqual(c.phone, "9847000000")
        vt = self.bus.vehicle_type
        r = self.office.patch(f"/api/office/r/vehicle-types/{vt.pk}/", {"is_active": False}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        vt.refresh_from_db(); self.assertFalse(vt.is_active)
        r = self.office.post("/api/office/r/vehicles/", {"registration_number": "KL01ZZ0001", "vehicle_type": vt.pk,
                                                         "status": "available", "fuel_type": "diesel", "year": 2022}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Vehicle.objects.get(registration_number="KL01ZZ0001").year, 2022)

    def test_api_root_describes_the_api(self):
        r = APIClient().get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["auth"]["login"], "/api/auth/login/")


@override_settings(MEDIA_ROOT="/tmp/fleetbooks-test-media")
class DriverProfileApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("9876543210", password="Drive@2026", role="driver")
        self.d = Driver.objects.create(name="John Mathew", phone="9876543210", salary=18000, user=self.user)
        other = User.objects.create_user("9123456780", password="Drive@2026", role="driver")
        self.other = Driver.objects.create(name="Thomas", phone="9123456780", user=other,
                                           photo=SimpleUploadedFile("t.png", b"\x89PNG0000", content_type="image/png"))
        self.boss = User.objects.create_superuser("boss", "b@x.in", "Pass@12345")
        self.api = APIClient()
        self.token = self.api.post("/api/auth/login/", {"username": "9876543210", "password": "Drive@2026"}, format="json").data["access"]
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_driver_edits_own_profile_but_not_office_fields(self):
        r = self.api.patch("/api/driver/profile/", {"phone": "98480 12345", "email": "john@example.com", "blood_group": "B+",
                                                    "emergency_contact_name": "Mary", "emergency_contact_phone": "9847000000",
                                                    "licence_number": "KL0720110012345", "licence_expiry": "2030-01-31",
                                                    "name": "Hacker", "salary": "99999", "photo": jpg("me.jpg")}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        self.d.refresh_from_db(); self.user.refresh_from_db()
        self.assertEqual((self.d.phone, self.d.blood_group, self.d.emergency_contact_name), ("98480 12345", "B+", "Mary"))
        self.assertEqual((self.d.name, self.d.salary), ("John Mathew", 18000))          # office-only fields untouched
        self.assertEqual((self.user.phone, self.user.email), ("98480 12345", "john@example.com"))
        self.assertTrue(self.d.photo.name.endswith(".jpg"))
        self.assertTrue(DriverActivity.objects.filter(driver=self.d, action="profile_updated").exists())
        self.assertTrue(Notification.objects.filter(user=self.boss, text__contains="licence").exists())
        c = APIClient()
        self.assertEqual(c.get(f"{self.d.photo.url}?token={self.token}").status_code, 200)       # own photo
        self.assertEqual(c.get(f"{self.other.photo.url}?token={self.token}").status_code, 404)   # someone else's

    def test_validation(self):
        r = self.api.patch("/api/driver/profile/", {"phone": "12"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("phone", r.data["errors"])
        bad = SimpleUploadedFile("x.pdf", b"%PDF-1.4", content_type="application/pdf")
        self.assertEqual(self.api.patch("/api/driver/profile/", {"photo": bad}, format="multipart").status_code, 400)
        self.d.refresh_from_db()
        self.assertEqual(self.d.phone, "9876543210")

    def test_change_password(self):
        r = self.api.post("/api/driver/profile/password/", {"current_password": "wrong", "new_password": "Newer@2026",
                                                            "confirm": "Newer@2026"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.api.post("/api/driver/profile/password/", {"current_password": "Drive@2026", "new_password": "Newer@2026",
                                                            "confirm": "Newer@2026"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Newer@2026"))


class PortabilityTests(TestCase):
    def test_no_linux_only_date_format_codes(self):
        """strftime codes with a dash (no zero padding) crash on Windows: ValueError: Invalid format string."""
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        bad = [f"{p.relative_to(root)}:{i}" for p in root.rglob("*.py") if "migrations" not in p.parts and ".venv" not in p.parts
               for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if re.search(r"%-[a-zA-Z]", line)]
        self.assertEqual(bad, [])
