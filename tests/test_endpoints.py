"""
Every endpoint, every role: correct status codes, no server errors on bad input, consistent JSON.

Runs against the demo business (seed_demo) so every list, detail and report has real data.
"""
import re

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import get_resolver
from django.urls.resolvers import URLResolver
from rest_framework.test import APIClient

from bookings.models import Booking, Customer
from finance.models import EMIInstallment, Expense, FuelEntry, Income, Loan, Maintenance
from fleet.models import Driver, Vehicle, VehicleDocument, VehicleType
from accounts.models import User
from office.resources import RESOURCES
from reports import registry

ROLES = {"anon": None, "driver": "driver", "staff": "staff", "manager": "manager", "accountant": "accounts", "admin": "admin"}
OPEN = {"/", "/api/", "/api/docs/", "/api/schema/"}            # no login needed
BAD_QUERY = {"page": "abc", "month": "13", "year": "x", "from": "bad", "to": "bad", "vehicle": "abc", "driver": "x",
             "search": "'\"%", "status": "zzz", "mode": "range"}


def _routes():
    def walk(patterns, prefix=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, prefix + str(p.pattern))
            else:
                yield prefix + str(p.pattern)
    out = set()
    for raw in walk(get_resolver().url_patterns):
        if raw.startswith("admin/") or "format>" in raw or raw.startswith("^.*") or "media/" in raw:
            continue
        out.add(raw.replace("(?P<pk>[^/.]+)", "<pk>").replace("^", "").replace("$", ""))
    return sorted(out)


@override_settings(MEDIA_ROOT="/tmp/fleetbooks-test-media")
class EveryEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        cache.clear()
        self.clients = {}
        for role, username in ROLES.items():
            c = APIClient(raise_request_exception=False)
            if username:
                r = c.post("/api/auth/login/", {"username": username, "password": "Demo@12345"}, format="json")
                self.assertEqual(r.status_code, 200, (username, r.content))
                c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
            self.clients[role] = c
        drv = Driver.objects.get(user__username="driver")
        self.ids = {
            "bookings": Booking.objects.first().pk, "customers": Customer.objects.first().pk, "drivers": Driver.objects.first().pk,
            "vehicles": Vehicle.objects.first().pk, "vehicle-types": VehicleType.objects.first().pk,
            "documents": VehicleDocument.objects.first().pk, "expenses": Expense.all_objects.first().pk,
            "fuel": FuelEntry.objects.first().pk, "income": Income.objects.first().pk, "maintenance": Maintenance.objects.first().pk,
            "loans": Loan.objects.first().pk, "emi": EMIInstallment.objects.first().pk, "users": User.objects.first().pk,
            "driver-payments": 1,
            "driver_trip": Booking.objects.filter(driver=drv).first().pk,
            "driver_expense": Expense.all_objects.filter(driver=drv).first().pk,
            "driver_problem": (Maintenance.objects.filter(driver=drv).first() or Maintenance.objects.first()).pk,
            "driver": drv.pk,
        }

    def expand(self, route):
        if "office/r/<slug:key>" in route:
            return [route.replace("<slug:key>", k).replace("<int:pk>", str(self.ids.get(k, 1))) for k in RESOURCES]
        if "reports/<slug:key>" in route:
            return [route.replace("<slug:key>", k) for k in registry.REPORTS]
        if route.startswith("api/driver/"):
            pk = self.ids["driver_trip"] if "trips" in route else self.ids["driver_expense"] if "expenses" in route \
                else self.ids["driver_problem"]
            return [route.replace("<int:pk>", str(pk))]
        m = re.match(r"api/(?:auth/)?([a-z-]+)/<pk>", route)
        if m:
            return [route.replace("<pk>", str(self.ids.get(m.group(1), 1)))]
        if "office/drivers/<int:pk>" in route:
            return [route.replace("<int:pk>", str(self.ids["driver"]))]
        if "office/expenses/<int:pk>" in route:
            return [route.replace("<int:pk>", str(self.ids["driver_expense"]))]
        return [route.replace("<int:pk>", "1")]

    def paths(self):
        out = []
        for route in _routes():
            for p in self.expand(route):
                p = "/" + p
                out.append(p if p.endswith("/") else p + "/")
        return out

    # ------------------------------------------------------------ permissions
    def test_permission_matrix(self):
        for path in self.paths():
            codes = {role: c.get(path).status_code for role, c in self.clients.items()}
            if path in OPEN:
                self.assertEqual(codes["anon"], 200, path)
                continue
            # not signed in: always 401 (or 405 for POST-only addresses that don't need a login, like /api/auth/login/)
            self.assertIn(codes["anon"], (401, 405), (path, codes))
            if path.startswith("/api/driver/"):
                for role in ("staff", "manager", "accountant", "admin"):
                    self.assertIn(codes[role], (403, 405), (path, role, codes))          # office users can't use the driver API
                self.assertIn(codes["driver"], (200, 405), (path, codes))
            elif path != "/api/auth/me/":
                self.assertIn(codes["driver"], (403, 405), (path, codes))                # drivers can't use office endpoints
                self.assertIn(codes["admin"], (200, 405), (path, codes))                 # admins can use all of them

    def test_role_limits(self):
        c = self.clients
        self.assertEqual(c["manager"].get("/api/income/").status_code, 403)            # managers don't see money in
        self.assertEqual(c["manager"].get("/api/reports/").status_code, 403)
        self.assertEqual(c["staff"].get("/api/expenses/").status_code, 403)
        self.assertEqual(c["accountant"].get("/api/auth/users/").status_code, 403)     # only admins manage users
        self.assertEqual(c["accountant"].get("/api/monthly-finance/").status_code, 200)
        self.assertEqual(c["staff"].post("/api/vehicles/", {}, format="json").status_code, 403)   # read-only role

    # ------------------------------------------------------------ robustness
    def test_no_server_errors_on_bad_input(self):
        bodies = [({}, "json"), ({"amount": "abc", "date": "x", "vehicle": "x", "booking": "y", "driver": "z", "action": "?"}, "json"),
                  ({}, "multipart")]
        for path in self.paths():
            for role in ("admin", "driver", "manager"):
                c = self.clients[role]
                for q in ({}, BAD_QUERY):
                    r = c.get(path, q)
                    self.assertLess(r.status_code, 500, (role, "GET", path, q, r.content[:300]))
                for method in ("post", "patch", "put"):
                    for data, fmt in bodies:
                        r = getattr(c, method)(path, data, format=fmt)
                        self.assertLess(r.status_code, 500, (role, method, path, data, r.content[:300]))
                r = c.generic("POST", path, "{not json", content_type="application/json")
                self.assertLess(r.status_code, 500, (role, "malformed JSON", path, r.content[:300]))

    def test_missing_and_invalid_ids(self):
        admin = self.clients["admin"]
        for module in ("vehicles", "bookings", "customers", "drivers", "fuel", "income", "loans"):
            self.assertEqual(admin.get(f"/api/{module}/999999/").status_code, 404, module)
            self.assertEqual(admin.get(f"/api/{module}/abc/").status_code, 404, module)
        self.assertEqual(admin.get("/api/office/r/vehicles/999999/").status_code, 404)
        self.assertEqual(admin.get("/api/office/r/nothing/").status_code, 404)
        self.assertEqual(admin.get("/api/reports/nothing/").status_code, 404)
        r = admin.post("/api/office/assign-trip/", {}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()), {"booking"})
        r = admin.post("/api/office/assign-trip/", {"booking": "x", "driver": 1}, format="json")
        self.assertEqual((r.status_code, list(r.json())), (400, ["booking"]))
        r = admin.post(f"/api/office/drivers/{self.ids['driver']}/assign-vehicle/", {"vehicle": "x"}, format="json")
        self.assertEqual((r.status_code, list(r.json())), (400, ["vehicle"]))

    def test_bad_filters_get_400_naming_the_field(self):
        admin = self.clients["admin"]
        for path in ("/api/monthly-finance/", "/api/reports/income/", "/api/office/r/fuel/"):
            for q, field in (({"month": "13"}, "month"), ({"year": "x"}, "year"), ({"vehicle": "abc"}, "vehicle"),
                             ({"date_from": "2026-99-01"}, "date_from")):
                r = admin.get(path, q)
                self.assertEqual(r.status_code, 400, (path, q))
                self.assertIn(field, r.json(), (path, q))
        self.assertEqual(admin.get("/api/reports/income/", {"export": "doc"}).status_code, 400)
        self.assertEqual(admin.get(f"/api/office/drivers/{self.ids['driver']}/overview/", {"month": "2026-13"}).status_code, 400)
        self.assertEqual(admin.get("/api/reports/income/", {"month": "10", "year": "2026"}).status_code, 200)

    def test_wrong_method_is_405_with_allow_header(self):
        admin = self.clients["admin"]
        r = admin.delete("/api/office/dashboard/")
        self.assertEqual(r.status_code, 405)
        self.assertIn("GET", r["Allow"])
        self.assertEqual(admin.get("/api/auth/login/").status_code, 405)

    def test_unknown_addresses_answer_json(self):
        for c in (APIClient(), self.clients["admin"]):
            r = c.get("/api/does-not-exist/")
            self.assertEqual(r.status_code, 404)
            self.assertEqual(r["Content-Type"], "application/json")
            self.assertIn("detail", r.json())

    def test_expired_or_fake_token_is_401(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        r = c.get("/api/office/dashboard/")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Bearer", r.get("WWW-Authenticate", ""))

    # ------------------------------------------------------------ crud round trip on standard endpoints
    def test_crud_round_trip(self):
        admin = self.clients["admin"]
        r = admin.post("/api/customers/", {"name": "Round Trip"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        cid = r.json()["id"]
        self.assertEqual(admin.get(f"/api/customers/{cid}/").json()["name"], "Round Trip")
        self.assertEqual(admin.put(f"/api/customers/{cid}/", {"name": "Round Trip 2", "phone": "1"}, format="json").status_code, 200)
        self.assertEqual(admin.patch(f"/api/customers/{cid}/", {"notes": "n"}, format="json").json()["notes"], "n")
        self.assertEqual(admin.post("/api/customers/", {}, format="json").status_code, 400)
        self.assertEqual(admin.delete(f"/api/customers/{cid}/").status_code, 204)
        self.assertEqual(admin.get(f"/api/customers/{cid}/").status_code, 404)
        lst = admin.get("/api/vehicles/").json()
        self.assertTrue({"count", "results"} <= set(lst))                      # paginated lists


@override_settings(LOGIN_THROTTLE_RATE="3/min")
class LoginLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_user("clerk", password="Pass@12345", role="staff")

    def tearDown(self):
        cache.clear()

    def test_too_many_login_attempts_get_429(self):
        c = APIClient()
        codes = [c.post("/api/auth/login/", {"username": "clerk", "password": "wrong"}, format="json").status_code for _ in range(4)]
        self.assertEqual(codes, [401, 401, 401, 429])
        r = c.post("/api/auth/login/", {"username": "clerk", "password": "Pass@12345"}, format="json")
        self.assertEqual(r.status_code, 429)                                    # even the right password waits
        self.assertIn("Retry-After", r)


class ApiDocsTests(TestCase):
    def test_schema_and_swagger_page(self):
        c = APIClient()
        r = c.get("/api/schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json")
        self.assertEqual(r.status_code, 200)
        schema = r.json()
        self.assertTrue(schema["openapi"].startswith("3."))
        for p in ("/api/auth/login/", "/api/driver/trips/{id}/start/", "/api/office/r/{key}/", "/api/reports/{key}/"):
            self.assertIn(p, schema["paths"])
        self.assertEqual(c.get("/api/docs/").status_code, 200)
        self.assertEqual(c.get("/").json()["docs"], "/api/docs/")
