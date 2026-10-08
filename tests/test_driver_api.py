"""Driver API (/api/driver/...): token login, full trip flow, ownership and role checks."""
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


@override_settings(MEDIA_ROOT="/tmp/fleetbooks-test-media")
class DriverApiTests(TestCase):
    def setUp(self):
        vt = VehicleType.objects.get_or_create(name="Tourist Bus")[0]
        self.bus = Vehicle.objects.create(registration_number="KL07AB1234", vehicle_type=vt)
        self.bus2 = Vehicle.objects.create(registration_number="KL07AB5678", vehicle_type=vt)
        cust = Customer.objects.create(name="ABC Travels")
        self.john, self.tom = (self._driver(n, u, v) for n, u, v in
                               [("John Mathew", "9876543210", self.bus), ("Thomas Joseph", "9123456780", self.bus2)])
        today = timezone.localdate()
        self.trip = Booking.objects.create(customer=cust, vehicle=self.bus, driver=self.john, trip_date=today,
                                           booking_amount=Decimal("30000"), pickup_location="Kochi", destination="Munnar")
        self.toms_trip = Booking.objects.create(customer=cust, vehicle=self.bus2, driver=self.tom, trip_date=today,
                                                booking_amount=Decimal("20000"))
        self.api = APIClient()

    def _driver(self, name, username, vehicle):
        user = User.objects.create_user(username, password="Drive@2026", role="driver")
        d = Driver.objects.create(name=name, phone=username, user=user)
        d.assign_vehicle(vehicle)
        return d

    def login(self, username="9876543210", password="Drive@2026"):
        r = self.api.post("/api/auth/login/", {"username": username, "password": password}, format="json")
        self.assertEqual(r.status_code, 200)
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")

    def test_full_trip_flow_over_api(self):
        self.login()
        r = self.api.get("/api/driver/dashboard/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["vehicle"]["registration_number"], "KL07AB1234")
        self.assertEqual(r.data["current_trip"]["status"], "assigned")
        self.assertNotIn("booking_amount", r.data["current_trip"])            # no company money for drivers
        self.assertEqual(self.api.post(f"/api/driver/trips/{self.trip.pk}/accept/").data["status"], "accepted")
        started = timezone.localtime() - timedelta(hours=3)
        r = self.api.post(f"/api/driver/trips/{self.trip.pk}/start/",
                          {"start_odometer": 1000, "start_location": "Kochi", "started_at": started.isoformat()}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "ongoing")
        # the same trip can't be started twice
        r = self.api.post(f"/api/driver/trips/{self.trip.pk}/start/", {"start_odometer": 1000, "start_location": "x"}, format="json")
        self.assertEqual(r.status_code, 409)
        r = self.api.post(f"/api/driver/trips/{self.trip.pk}/complete/", {"end_odometer": 900, "end_location": "Munnar"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("end_odometer", r.data["errors"])
        r = self.api.post(f"/api/driver/trips/{self.trip.pk}/complete/",
                          {"end_odometer": 1142, "end_location": "Munnar", "trip_notes": "Fine"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["status"], r.data["total_km"]), ("completed", 142))
        self.assertTrue(r.data["duration"])

    def test_fuel_expense_and_problem_with_uploads(self):
        self.login()
        receipt = SimpleUploadedFile("bill.jpg", b"\xff\xd8\xff" + b"0" * 50, content_type="image/jpeg")
        r = self.api.post("/api/driver/fuel/", {"vehicle": self.bus.pk, "booking": self.trip.pk,
                                                "date": timezone.localdate().isoformat(), "fuel_type": "diesel",
                                                "litres": "40", "rate": "95.50", "payment_method": "cash",
                                                "receipt": receipt}, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Decimal(str(r.data["total_amount"])), Decimal("3820.00"))
        self.assertTrue(r.data["receipt"].endswith(".jpg"))
        self.assertEqual(Expense.objects.get(fuel_entry_id=r.data["id"]).amount, Decimal("3820.00"))
        r = self.api.post("/api/driver/expenses/", {"vehicle": self.bus.pk, "date": timezone.localdate().isoformat(),
                                                    "category": "parking", "amount": "150", "payment_method": "cash"})
        self.assertEqual((r.status_code, r.data["status"]), (201, "pending"))
        self.assertFalse(Expense.objects.filter(pk=r.data["id"]).exists())    # pending ⇒ not in finance yet
        self.assertEqual(self.api.post(f"/api/driver/expenses/{r.data['id']}/withdraw/").status_code, 204)
        bad = SimpleUploadedFile("x.exe", b"MZ", content_type="application/octet-stream")
        r = self.api.post("/api/driver/maintenance/", {"vehicle": self.bus.pk, "date": timezone.localdate().isoformat(),
                                                       "problem_category": "brake", "priority": "emergency",
                                                       "description": "Brake failure", "photo": bad}, format="multipart")
        self.assertEqual(r.status_code, 400)
        r = self.api.post("/api/driver/maintenance/", {"vehicle": self.bus.pk, "date": timezone.localdate().isoformat(),
                                                       "problem_category": "brake", "priority": "emergency",
                                                       "description": "Brake failure"}, format="json")
        self.assertEqual((r.status_code, r.data["status"]), (201, "reported"))
        for url in ["/api/driver/fuel/", "/api/driver/expenses/", "/api/driver/maintenance/", "/api/driver/vehicle/",
                    "/api/driver/profile/", "/api/driver/notifications/", "/api/driver/trips/?show=all"]:
            self.assertEqual(self.api.get(url).status_code, 200, url)

    def test_ownership_and_roles(self):
        self.login()
        toms_exp = Expense.all_objects.create(status="pending", driver=self.tom, vehicle=self.bus2, category="toll", amount=50)
        toms_problem = Maintenance.objects.create(vehicle=self.bus2, driver=self.tom, status="reported", description="x")
        for method, url in [("get", f"/api/driver/trips/{self.toms_trip.pk}/"), ("post", f"/api/driver/trips/{self.toms_trip.pk}/accept/"),
                            ("post", f"/api/driver/trips/{self.toms_trip.pk}/start/"), ("get", f"/api/driver/expenses/{toms_exp.pk}/"),
                            ("post", f"/api/driver/expenses/{toms_exp.pk}/withdraw/"), ("get", f"/api/driver/maintenance/{toms_problem.pk}/")]:
            self.assertEqual(getattr(self.api, method)(url).status_code, 404, url)
        # another driver's trip/vehicle posted in a form is refused
        r = self.api.post("/api/driver/fuel/", {"vehicle": self.bus2.pk, "booking": self.toms_trip.pk, "date": timezone.localdate().isoformat(),
                                                "fuel_type": "diesel", "litres": "5", "rate": "90", "payment_method": "cash"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(FuelEntry.objects.exists())
        ids = [t["id"] for t in self.api.get("/api/driver/trips/?show=all").data["results"]]
        self.assertEqual(ids, [self.trip.pk])
        # drivers are refused by every admin API
        for url in ["/api/income/", "/api/expenses/", "/api/dashboard/", "/api/drivers/", "/api/reports/profit-loss/"]:
            self.assertEqual(self.api.get(url).status_code, 403, url)
        # office users (even admins) and anonymous users can't use the Driver API
        User.objects.create_superuser("boss", "b@x.in", "Pass@12345")
        self.api.credentials()
        self.assertEqual(self.api.get("/api/driver/dashboard/").status_code, 401)
        self.login("boss", "Pass@12345")
        self.assertEqual(self.api.get("/api/driver/dashboard/").status_code, 403)
        # a deactivated driver is locked out even with a token issued earlier
        self.login()
        self.john.status = "inactive"; self.john.save()
        self.assertEqual(self.api.get("/api/driver/dashboard/").status_code, 403)
