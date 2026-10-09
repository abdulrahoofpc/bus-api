"""Office API (/api/office/...): definitions by role, saving with side effects, approvals, accounts, files by token."""
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from bookings.models import Booking, Customer
from finance.models import Expense, FuelEntry, Income
from fleet.models import Driver, Vehicle, VehicleAssignment, VehicleType
from portal.models import DriverActivity, Notification


@override_settings(MEDIA_ROOT="/tmp/fleetbooks-test-media")
class OfficeApiTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("boss", "b@x.in", "Pass@12345")
        self.manager = User.objects.create_user("mgr", password="Pass@12345", role="manager")
        vt = VehicleType.objects.get_or_create(name="Tourist Bus")[0]
        self.bus = Vehicle.objects.create(registration_number="KL07AB1234", vehicle_type=vt, fuel_type="diesel")
        self.bus2 = Vehicle.objects.create(registration_number="KL07AB5678", vehicle_type=vt)
        self.customer = Customer.objects.create(name="ABC Travels")
        self.api = APIClient()
        self.login("boss", "Pass@12345")
        self.today = timezone.localdate().isoformat()

    def login(self, u, p):
        token = self.api.post("/api/auth/login/", {"username": u, "password": p}, format="json").data["access"]
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return token

    def test_paging_never_repeats_rows(self):
        for i in range(5):
            Customer.objects.create(name=f"Customer {i}")
        seen = []
        for page in (1, 2, 3, 4, 99):
            r = self.api.get("/api/office/r/customers/", {"page": page, "page_size": 2}).json()
            self.assertEqual(r["count"], 6)
            seen += [row["id"] for row in r["results"]]
            if page >= 4:
                self.assertEqual(r["results"], [])  # past the end: empty, not the last page again
                self.assertIsNone(r["next"])
        self.assertEqual(len(seen), 6)
        self.assertEqual(len(set(seen)), 6)

    def test_definitions_follow_role(self):
        defs = self.api.get("/api/office/resources/").json()
        self.assertIn("users", defs)
        fuel = defs["fuel"]
        self.assertTrue(fuel["period"])
        self.assertTrue(any(f["name"] == "vehicle" and f["choices"] for f in fuel["fields"]))
        self.login("mgr", "Pass@12345")
        defs = self.api.get("/api/office/resources/").json()
        self.assertNotIn("users", defs)          # managers can't manage users
        self.assertNotIn("income", defs)         # …or see income

    def test_driver_created_in_app_gets_login_and_history_like_web(self):
        r = self.api.post("/api/office/r/drivers/", {"name": "John Mathew", "phone": "9876543210", "status": "active",
                                                     "salary": "18000", "assigned_vehicle": self.bus.pk,
                                                     "login_username": "9876543210", "login_password": "Drive@2026"}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        d = Driver.objects.get(name="John Mathew")
        self.assertTrue(d.user and d.user.check_password("Drive@2026"))
        self.assertEqual(VehicleAssignment.objects.filter(driver=d, vehicle=self.bus).count(), 1)
        # duplicate login name is refused with a field error, nothing half-saved
        r = self.api.post("/api/office/r/drivers/", {"name": "Copy", "status": "active", "salary": "0",
                                                     "login_username": "9876543210", "login_password": "Drive@2026"}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertIn("login_username", r.json()["errors"])
        self.assertFalse(Driver.objects.filter(name="Copy").exists())

    def test_booking_saved_in_app_creates_advance_income_and_notifies_driver(self):
        user = User.objects.create_user("drv", password="Drive@2026", role="driver")
        d = Driver.objects.create(name="Ravi", user=user)
        r = self.api.post("/api/office/r/bookings/", {"customer": self.customer.pk, "vehicle": self.bus.pk, "driver": d.pk, "booking_date": self.today,
                                                      "trip_date": self.today, "return_date": self.today, "number_of_days": "1",
                                                      "booking_amount": "30000", "advance": "5000", "advance_method": "cash",
                                                      "trip_status": "upcoming", "pickup_location": "Kochi", "destination": "Munnar"}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        b = Booking.objects.get(pk=r.json()["id"])
        self.assertEqual(Income.objects.filter(booking=b).first().amount, Decimal("5000.00"))
        self.assertTrue(Notification.objects.filter(user=user, text__contains="New trip assigned").exists())
        # partial edit: only the field sent changes
        r = self.api.post(f"/api/office/r/bookings/{b.pk}/", {"destination": "Thekkady"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        b.refresh_from_db()
        self.assertEqual((b.destination, b.booking_amount), ("Thekkady", Decimal("30000.00")))

    def test_list_filters_totals_and_locked_rows(self):
        FuelEntry.objects.create(date=self.today, vehicle=self.bus, litres=Decimal("40"), rate=Decimal("100"), fuel_type="diesel")
        j = self.api.get("/api/office/r/fuel/", {"mode": "all"}).json()
        self.assertEqual(j["count"], 1)
        self.assertTrue(any(t["label"] == "Total cost" for t in j["totals"]))
        exp = j and Expense.objects.get(source="fuel")
        r = self.api.get(f"/api/office/r/expenses/{exp.pk}/").json()
        self.assertTrue(r["_locked"])                                  # linked expense: change it on the fuel entry
        self.assertEqual(self.api.post(f"/api/office/r/expenses/{exp.pk}/", {"amount": "1"}, format="multipart").status_code, 400)
        self.assertEqual(self.api.delete(f"/api/office/r/expenses/{exp.pk}/").status_code, 400)
        r = self.api.post("/api/office/r/fuel/", {"vehicle": self.bus.pk, "date": self.today, "litres": "abc"}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertIn("litres", r.json()["errors"])

    def test_approvals_accounts_assignments_activity_notifications(self):
        user = User.objects.create_user("drv", password="Drive@2026", role="driver")
        d = Driver.objects.create(name="Ravi", user=user)
        e = Expense.all_objects.create(status="pending", driver=d, vehicle=self.bus, category="toll", amount=Decimal("300"))
        self.assertEqual(len(self.api.get("/api/office/expense-approvals/").json()["pending"]), 1)
        self.assertEqual(self.api.post(f"/api/office/expenses/{e.pk}/decide/", {"action": "reject"}, format="json").status_code, 400)
        self.assertEqual(self.api.post(f"/api/office/expenses/{e.pk}/decide/", {"action": "approve"}, format="json").status_code, 200)
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())       # now counts
        r = self.api.post(f"/api/office/drivers/{d.pk}/account/", {"action": "reset", "password": "NewPass@2026"}, format="json")
        self.assertEqual(r.status_code, 200)
        r = self.api.post(f"/api/office/drivers/{d.pk}/account/", {"action": "deactivate"}, format="json")
        self.assertFalse(r.json()["account"]["is_active"])
        self.api.post(f"/api/office/drivers/{d.pk}/assign-vehicle/", {"vehicle": self.bus2.pk}, format="json")
        d.refresh_from_db()
        self.assertEqual(d.assigned_vehicle, self.bus2)
        b = Booking.objects.create(customer=self.customer, vehicle=self.bus, trip_date=timezone.localdate(), booking_amount=1000)
        self.assertEqual(self.api.post("/api/office/assign-trip/", {"booking": b.pk, "driver": d.pk}, format="json").status_code, 200)
        b.refresh_from_db()
        self.assertEqual(b.driver, d)
        acts = self.api.get("/api/office/activity/", {"driver": d.pk}).json()
        self.assertGreaterEqual(acts["count"], 4)
        self.assertEqual(self.api.get(f"/api/office/drivers/{d.pk}/overview/").status_code, 200)
        self.assertIn("results", self.api.get("/api/office/notifications/").json())
        self.assertEqual(DriverActivity.objects.filter(driver=d, action="expense_approved").count(), 1)

    def test_roles_and_files_by_token(self):
        self.login("mgr", "Pass@12345")
        self.assertEqual(self.api.get("/api/office/expense-approvals/").status_code, 403)
        self.assertEqual(self.api.get("/api/office/r/income/").status_code, 403)
        user = User.objects.create_user("drv", password="Drive@2026", role="driver")
        d = Driver.objects.create(name="Ravi", user=user, assigned_vehicle=self.bus)
        token = self.login("drv", "Drive@2026")
        self.assertEqual(self.api.get("/api/office/dashboard/").status_code, 403)   # drivers never reach office data
        opts = self.api.get("/api/driver/form-options/").json()
        self.assertEqual(opts["vehicles"], [[str(self.bus.pk), self.bus.label]])
        self.assertTrue(opts["expense_categories"])
        e = Expense.all_objects.create(status="pending", driver=d, category="toll", amount=5,
                                       receipt=SimpleUploadedFile("r.jpg", b"\xff\xd8x"))
        other = Expense.all_objects.create(status="pending", category="toll", amount=5, receipt=SimpleUploadedFile("o.jpg", b"\xff\xd8x"))
        c = APIClient()
        self.assertEqual(c.get(f"{e.receipt.url}?token={token}").status_code, 200)       # own receipt in the app
        self.assertEqual(c.get(f"{other.receipt.url}?token={token}").status_code, 404)   # someone else's
        self.assertEqual(c.get(e.receipt.url).status_code, 401)                          # no token → refused
