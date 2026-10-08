"""
Loads realistic sample data so you can explore the system:
    python manage.py seed_demo
Creates demo users (password: Demo@12345) for each role.
Do NOT run this on your live database.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import BusinessSettings, User
from bookings.models import Booking, Customer
from common.dates import add_months, today
from finance.models import DriverPayment, Expense, FuelEntry, Income, Loan, Maintenance
from fleet.models import Driver, Vehicle, VehicleDocument, VehicleType


class Command(BaseCommand):
    help = "Load demo vehicles, trips and accounts for trying out the system."

    def handle(self, *args, **opts):
        if Vehicle.objects.exists():
            self.stdout.write(self.style.WARNING("Vehicles already exist - demo data not loaded."))
            return
        random.seed(7)
        with transaction.atomic():
            self._load()
        self.stdout.write(self.style.SUCCESS("Demo data loaded. Office login: admin / Demo@12345 · Driver login: driver / Demo@12345"))

    def _load(self):
        t = today()
        biz = BusinessSettings.load()
        biz.name, biz.phone, biz.address = "Deccan Tours & Travels", "+91 98480 00000", "Ameerpet, Hyderabad"
        biz.save()
        for username, role in [("admin", "admin"), ("accounts", "accountant"), ("manager", "manager"), ("staff", "staff")]:
            u, _ = User.objects.get_or_create(username=username, defaults={"role": role, "first_name": username.title()})
            u.role = role
            u.is_staff = username == "admin"
            u.is_superuser = username == "admin"
            u.set_password("Demo@12345")
            u.save()

        for i, name in enumerate(["Tourist Bus", "Taxi", "Car", "Van / Traveller", "SUV", "Other"], start=1):
            VehicleType.objects.get_or_create(name=name, defaults={"sort_order": i * 10})
        types = {vt.name: vt for vt in VehicleType.objects.all()}
        fleet = [
            ("Bus 1", "TS09UB4521", "Tourist Bus", "Ashok Leyland", "Falcon", 2021, 45, "diesel", 3800000),
            ("Bus 2", "TS09UB7810", "Tourist Bus", "BharatBenz", "1017", 2023, 41, "diesel", 4200000),
            ("Taxi 1", "TS08UC1102", "Taxi", "Maruti", "Dzire Tour", 2022, 4, "cng", 780000),
            ("Taxi 2", "TS08UC3390", "Taxi", "Hyundai", "Aura", 2023, 4, "petrol", 820000),
            ("Innova", "TS07UD5566", "SUV", "Toyota", "Innova Crysta", 2022, 7, "diesel", 2300000),
            ("Traveller", "TS10UE2044", "Van / Traveller", "Force", "Traveller 3350", 2021, 17, "diesel", 1650000),
            ("Car 1", "TS09EF9001", "Car", "Honda", "City", 2020, 4, "petrol", 1150000),
        ]
        vehicles = []
        for short, reg, vt, brand, model, year, seats, fuel, price in fleet:
            vehicles.append(Vehicle.objects.create(
                short_name=short, registration_number=reg, vehicle_type=types[vt], brand=brand, model=model,
                year=year, seating_capacity=seats, fuel_type=fuel, purchase_price=price,
                purchase_date=t.replace(year=year, month=4, day=10)))
        vehicles[-1].status = "maintenance"
        vehicles[-1].save()

        names = ["Ramesh Kumar", "Srinivas Rao", "Mohammed Irfan", "Venkatesh", "Suresh Reddy", "Anil Yadav", "Prakash"]
        drivers = [Driver.objects.create(
            name=n, phone=f"+91 9{random.randint(100000000, 999999999)}", licence_number=f"TS{random.randint(10**11, 10**12)}",
            licence_expiry=t + timedelta(days=random.choice([12, 45, 200, 400, 700])), salary=random.choice([18000, 20000, 22000, 25000]),
            joining_date=t - timedelta(days=random.randint(200, 1500)), assigned_vehicle=vehicles[i],
        ) for i, n in enumerate(names)]

        customers = [Customer.objects.create(name=n, phone=f"+91 8{random.randint(100000000, 999999999)}", company_name=c)
                     for n, c in [("Lakshmi Narayana", ""), ("Priya Sharma", "Infotech Solutions Pvt Ltd"),
                                  ("Kiran Kumar", ""), ("Ravi Teja", "Sunrise School"), ("Fatima Begum", ""),
                                  ("Arjun Mehta", "Mehta Events"), ("Swathi", ""), ("Harsha Vardhan", "HV Pharma")]]
        routes = [("Hyderabad", "Srisailam"), ("Hyderabad", "Tirupati"), ("Hyderabad", "Warangal"),
                  ("Airport", "Gachibowli"), ("Hyderabad", "Vijayawada"), ("Hyderabad", "Bidar"), ("Secunderabad", "Yadagirigutta")]

        # Six months of trips
        start = add_months(t.replace(day=1), -5)
        day = start
        odo = {}
        while day <= t + timedelta(days=10):
            for v, drv in zip(vehicles[:6], drivers[:6]):
                if random.random() > 0.18:
                    continue
                big = v.vehicle_type.name in ("Tourist Bus", "Van / Traveller")
                days = random.choice([1, 1, 2, 3]) if big else 1
                ret = day + timedelta(days=days - 1)
                amount = Decimal(random.choice([28000, 35000, 42000, 55000, 64000]) if big else random.choice([2500, 3200, 4500, 6800, 9000]))
                if day > t:
                    status = "upcoming"
                elif ret >= t:
                    status = "ongoing"
                else:
                    status = random.choice(["completed"] * 12 + ["cancelled"])
                advance = (amount * Decimal(random.choice([0, "0.2", "0.3", "0.5"]))).quantize(Decimal("1"))
                pickup, dest = random.choice(routes)
                try:
                    b = Booking.objects.create(
                        booking_date=day - timedelta(days=random.randint(1, 10)), customer=random.choice(customers),
                        vehicle=v, driver=drv, trip_date=day, return_date=ret, pickup_location=pickup,
                        destination=dest, booking_amount=amount, advance=advance, trip_status=status)
                except Exception:
                    continue
                if status == "completed" and random.random() > 0.15:
                    Income.objects.create(date=ret, booking=b, income_type="trip", amount=amount - advance,
                                          payment_method=random.choice(["cash", "upi", "bank"]))
                if status in ("completed", "ongoing"):
                    litres = Decimal(random.randint(40, 180) if big else random.randint(15, 40))
                    # Odometer climbs realistically: ~4 km/l for buses, ~13 km/l for smaller vehicles.
                    odo[v.id] = odo.get(v.id, random.randint(30000, 90000)) + int(float(litres) * (random.uniform(3.5, 4.5) if big else random.uniform(11, 15)))
                    FuelEntry.objects.create(date=day, vehicle=v, driver=drv, booking=b, fuel_type=v.fuel_type if v.fuel_type != "cng" else "cng",
                                             litres=litres, rate=Decimal("96.50") if v.fuel_type != "petrol" else Decimal("109.60"),
                                             odometer=odo[v.id], fuel_station="HP, Kukatpally")
                    if big:
                        Expense.objects.create(date=day, vehicle=v, booking=b, category="toll",
                                               amount=Decimal(random.choice([450, 820, 1240])), description="Toll plaza charges")
            day += timedelta(days=1)

        # Monthly running costs
        m = start
        while m <= t:
            for drv in drivers:
                if add_months(m, 1) <= t or t.day > 5:
                    DriverPayment.objects.create(driver=drv, date=min(m.replace(day=5), t) if m.month != t.month else min(m.replace(day=5), t),
                                                 payment_type="salary", salary_month=m, amount=drv.salary if m.month != t.month else drv.salary - 5000,
                                                 payment_method="bank")
            Expense.objects.create(date=m.replace(day=3), category="office", amount=Decimal("15000"), description="Office rent")
            Expense.objects.create(date=m.replace(day=10), category="parking", vehicle=vehicles[0], amount=Decimal("3000"), description="Monthly parking")
            m = add_months(m, 1)

        for v in vehicles:
            Maintenance.objects.create(vehicle=v, date=t - timedelta(days=random.randint(20, 120)), maintenance_type="service",
                                       workshop="Sri Sai Motors", parts_cost=Decimal(random.randint(3000, 18000)),
                                       labour_cost=Decimal(random.randint(1000, 5000)), description="Periodic service, oil and filters",
                                       next_service_date=t + timedelta(days=random.choice([5, 18, 40, 75])))

        for v, bank, amt, rate, months in [(vehicles[0], "HDFC Bank", 2800000, "9.5", 60), (vehicles[1], "Shriram Finance", 3400000, "11.25", 60),
                                           (vehicles[4], "ICICI Bank", 1600000, "9.0", 48), (vehicles[2], "Tata Capital", 600000, "12.0", 36)]:
            loan = Loan.objects.create(vehicle=v, lender=bank, loan_amount=amt, down_payment=v.purchase_price - amt,
                                       interest_rate=Decimal(rate), start_date=add_months(t, -14).replace(day=1),
                                       tenure_months=months, first_emi_date=add_months(t, -13).replace(day=7))
            for inst in loan.installments.filter(due_date__lt=t - timedelta(days=0)).order_by("number"):
                if inst.due_date < t - timedelta(days=2) or v != vehicles[2]:
                    inst.mark_paid(inst.due_date, method="bank")

        docs = [("insurance", 300), ("permit", 25), ("fitness", 150), ("road_tax", 60), ("pollution", 5)]
        for i, v in enumerate(vehicles):
            for j, (kind, days) in enumerate(docs):
                offset = days - (i * 11 + j * 7) % 40 - (35 if i == 2 and kind == "pollution" else 0)
                VehicleDocument.objects.create(vehicle=v, doc_type=kind, document_number=f"{kind[:3].upper()}-{random.randint(10000, 99999)}",
                                               expiry_date=t + timedelta(days=offset),
                                               amount=Decimal({"insurance": 45000, "permit": 12000, "fitness": 1500,
                                                               "road_tax": 18000, "pollution": 400}[kind]))

        # ---------------- Driver demo: logins, history, odometers, submissions
        from bookings.models import Booking as _B
        from finance.models import Expense as _E, Maintenance as _M
        from fleet.models import VehicleAssignment
        from portal import services as ps
        admin_user = User.objects.get(username="admin")
        for d, username in [(drivers[0], "driver"), (drivers[1], "driver2")]:
            u = User.objects.filter(username=username).first() or User(username=username)
            u.first_name, u.role, u.phone, u.is_active = d.name, "driver", d.phone, True
            u.set_password("Demo@12345")
            u.save()
            d.user = u
            d.save(update_fields=["user"])
        for d in drivers:
            if not VehicleAssignment.objects.filter(driver=d).exists():
                VehicleAssignment.objects.create(driver=d, vehicle=d.assigned_vehicle, start_date=d.joining_date,
                                                 assigned_by=admin_user, notes="Initial assignment")
        # Completed trips get realistic odometer readings and times (for KM and duration reports)
        from django.utils import timezone as _tz
        import datetime as _dt
        odo = {}
        for b in _B.objects.filter(trip_status="completed").order_by("trip_date", "id"):
            start_km = odo.get(b.vehicle_id, random.randint(40000, 90000))
            km = random.randint(80, 260) * max(b.number_of_days, 1)
            begin = _tz.make_aware(_dt.datetime.combine(b.trip_date, _dt.time(random.randint(5, 9), random.choice([0, 15, 30]))))
            finish = _tz.make_aware(_dt.datetime.combine(b.return_date or b.trip_date, _dt.time(random.randint(17, 22), random.choice([0, 20, 40]))))
            _B.objects.filter(pk=b.pk).update(accepted_at=begin - _dt.timedelta(days=1), started_at=begin, start_odometer=start_km,
                                             start_location=b.pickup_location or "Hyderabad", completed_at=finish,
                                             end_odometer=start_km + km, end_location=b.destination or "Hyderabad")
            odo[b.vehicle_id] = start_km + km + random.randint(10, 60)
        _B.objects.filter(trip_status="ongoing").update(started_at=_tz.now() - _dt.timedelta(hours=6), accepted_at=_tz.now() - _dt.timedelta(days=1))
        # Ramesh (login: driver) has a new trip to accept and some submissions for the office to review
        ramesh = drivers[0]
        nxt = t + timedelta(days=1)
        if not _B.objects.filter(driver=ramesh, trip_status="upcoming", accepted_at__isnull=True).exists():
            new_trip = _B.objects.create(customer=customers[1], vehicle=ramesh.assigned_vehicle, driver=ramesh, trip_date=nxt,
                                         return_date=nxt + timedelta(days=1), pickup_location="Hyderabad", destination="Srisailam",
                                         booking_amount=Decimal("38000"), advance=Decimal("10000"), trip_status="upcoming")
            ps.notify_driver(ramesh, f"New trip assigned: {new_trip.booking_number} on {nxt:%d %b} (Srisailam)", f"/driver/trips/{new_trip.pk}/")
            ps.log(ramesh, "trip_assigned", f"Trip {new_trip.booking_number} assigned to {ramesh.name}", admin_user,
                   new_trip.vehicle, new_trip, status="assigned")
        for b in _B.objects.filter(driver__user__isnull=False, trip_status="upcoming", accepted_at__isnull=True):
            if not b.driver.user.notifications.filter(url=f"/driver/trips/{b.pk}/").exists():
                ps.notify_driver(b.driver, f"New trip assigned: {b.booking_number} on {b.trip_date:%d %b} "
                                           f"({b.destination or b.customer.name})", f"/driver/trips/{b.pk}/")
        last = _B.objects.filter(driver=ramesh, trip_status="completed").order_by("-trip_date").first()
        e1 = _E.all_objects.create(status="pending", driver=ramesh, submitted_by=ramesh.user, vehicle=ramesh.assigned_vehicle,
                                   booking=last, category="food_allowance", amount=Decimal("850"), date=t,
                                   description="Meals on the Tirupati trip")
        e2 = _E.all_objects.create(status="pending", driver=ramesh, submitted_by=ramesh.user, vehicle=ramesh.assigned_vehicle,
                                   booking=last, category="toll", amount=Decimal("465"), date=t - timedelta(days=1),
                                   description="Outer ring road + NH65 tolls")
        _E.all_objects.create(status="rejected", driver=ramesh, submitted_by=ramesh.user, vehicle=ramesh.assigned_vehicle,
                              category="cleaning", amount=Decimal("1200"), date=t - timedelta(days=6),
                              description="Interior wash", rejection_reason="Cleaning is covered by the monthly contract.",
                              reviewed_by=admin_user, reviewed_at=_tz.now() - _dt.timedelta(days=5))
        m = _M.objects.create(vehicle=ramesh.assigned_vehicle, date=t, maintenance_type="repair", problem_category="brake",
                              priority="high", status="reported", driver=ramesh, reported_by=ramesh.user,
                              description="Brakes making a grinding noise when stopping from high speed.")
        for e in (e1, e2):
            ps.log(ramesh, "expense_submitted", f"{ramesh.name} submitted {e.get_category_display().lower()} expense ₹{e.amount:,.0f}",
                   ramesh.user, e.vehicle, e.booking, e.amount, "pending")
            ps.notify_office("expenses", f"{ramesh.name} submitted an expense of ₹{e.amount:,.0f} for approval", "/expenses/review/")
        ps.log(ramesh, "problem_reported", f"{ramesh.name} reported a brake problem (High)", ramesh.user, m.vehicle, status="reported")
        ps.notify_office("maintenance", f"{ramesh.name} reported a problem on {m.vehicle.label}", f"/maintenance/{m.pk}/edit/")
