# FleetBooks API (backend)

The REST API for FleetBooks, a fleet and finance system for tourist transport. It is built with Python, Django and Django REST Framework.

This folder is **only the backend**: it has no web pages. The **FleetBooks app** (the separate `fleetbooks-frontend` folder, a React Native / Expo app for Android and iPhone) uses this API. Both office staff and drivers sign in through the app.

| Who | Uses | API |
|---|---|---|
| Admin / Superadmin (plus optional Accountant, Manager, Staff) | Office section of the app | `/api/office/…` and the module endpoints `/api/…` |
| Driver | Driver section of the app | `/api/driver/…` only |

---

## 1. Run it on Windows (first time)

You need **Python 3.11 or newer** from python.org. Tick **"Add python.exe to PATH"** during install.

Open **Command Prompt** in this folder (the one with `manage.py`):

```bat
cd C:\Users\ACM\Desktop\fleetbooks-backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py seed_demo
```

Let phones reach it:

1. Run `ipconfig` and note your Wi-Fi **IPv4 Address**, e.g. `192.168.5.28`.
2. Open `.env` and add that address:
   ```
   DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,192.168.5.28
   ```
3. Start the API on all network interfaces:
   ```bat
   python manage.py runserver 0.0.0.0:8000
   ```
4. When Windows Firewall asks, allow **Python** on **Private networks**.

Check it: open `http://192.168.5.28:8000/` in the phone's browser. You should see `{"name": "FleetBooks API", …}`. In the app, the server address is `http://192.168.5.28:8000`.

Demo logins (created by `seed_demo` in an **empty** database):

| Login | Password | Section of the app |
|---|---|---|
| `admin` | `Demo@12345` | Office (superadmin) |
| `driver` (Ramesh Kumar), `driver2` (Srinivas Rao) | `Demo@12345` | Driver |
| `accounts`, `manager`, `staff` | `Demo@12345` | Office with limited access |

To start clean instead, skip `seed_demo` and run `python manage.py createsuperuser`.

**Next time** you only need:

```bat
cd C:\Users\ACM\Desktop\fleetbooks-backend
.venv\Scripts\activate
python manage.py runserver 0.0.0.0:8000
```

`/admin/` is Django's built-in admin, for superusers who need to fix data directly. It's optional, and the app doesn't use it.

---

## 2. Run it on a server

**Small VPS / DigitalOcean droplet (even 512 MB RAM):** follow **[deploy/DEPLOY.md](deploy/DEPLOY.md)**. It sets up PostgreSQL, gunicorn, nginx and free HTTPS, step by step.

**Docker** (needs 1 GB RAM or more):

```bash
cp .env.docker.example .env.docker      # then edit the passwords and your domain
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker exec api python manage.py createsuperuser
```

This starts two containers: PostgreSQL and the API (gunicorn). The API is at `http://localhost:8080/`. Uploaded receipts and photos are kept in the `media` volume.

For use over the internet, put the API behind **HTTPS** (e.g. Caddy or nginx) and set `DJANGO_SECURE_COOKIES=true`. Then enter the `https://…` address in the app.

---

## 3. Try the API (Swagger and Postman)

**In the browser:** open **`http://<server>/api/docs/`**, e.g. `http://192.168.5.28:8000/api/docs/` or `http://139.59.3.23/api/docs/`. It lists every endpoint with its fields.
1. Open **auth → POST /api/auth/login/**, click **Try it out**, enter a username and password, and click **Execute**.
2. Copy the `access` value, click **Authorize** at the top, paste it under **jwtAuth**, and click **Authorize**.
3. Any endpoint now works with **Try it out → Execute**.

The OpenAPI file is at `/api/schema/`. To turn the docs off on a public server, set `API_DOCS=false` in `.env`.

**In Postman:** import `postman/FleetBooks-API.postman_collection.json`.
1. Under the collection's **Variables**, set `baseUrl` (e.g. `http://139.59.3.23`) and the logins. The demo logins are filled in.
2. Right-click the **"1. Smoke test"** folder, choose **Run**, and click **Run**.

The smoke test makes 25 requests with 38 checks. It covers login, token refresh, dashboards, a full create/read/update/delete on a test customer (which it deletes again), validation errors, a PDF report, the driver API and the security rules (401/403/404). **"2. All endpoints"** has every endpoint for trying by hand.

The same smoke test runs from a terminal, without Postman:

```bat
npx newman run postman/FleetBooks-API.postman_collection.json --folder "1. Smoke test" --env-var baseUrl=http://localhost:8000
```

After changing endpoints, rebuild the collection with:

```bat
python manage.py spectacular --format openapi-json --file postman/openapi.json
python postman/build_collection.py
```

## 4. How to call the API

**Log in** to get tokens (JWT):

```http
POST /api/auth/login/
{"username": "admin", "password": "Demo@12345"}
→ {"access": "…", "refresh": "…"}
```

Send `Authorization: Bearer <access>` with every request. When the access token expires (after 8 hours by default), get a new one:

```http
POST /api/auth/refresh/
{"refresh": "…"}
→ {"access": "…", "refresh": "…"}
```

Other rules:

- **Files** are sent as `multipart/form-data`. Uploads accept photos, PDFs and short videos up to 10 MB.
- **Uploaded files** come back as `/media/…` URLs. Open them with the same Bearer header, or add `?token=<access>` to the URL to open one in a viewer.
- **Lists** from the module endpoints (`/api/vehicles/` …) are paginated: `{"count", "next", "previous", "results"}`, with `?page=`.
- **Every answer is JSON**, including errors and unknown addresses (except the PDF/Excel report downloads).
- **Errors:**
  - `400` for invalid data. Body: `{"field": ["message"]}`, `{"errors": {"field": ["message"]}, "detail": "…"}` (driver and office forms) or `{"detail": "…"}`. Bad filters (e.g. `?month=13`, `?vehicle=abc`) also give 400 and name the field.
  - `401` when the token is missing, wrong or expired. Use the refresh token or log in again.
  - `403` when your role isn't allowed.
  - `404` for records or addresses that don't exist, **or records that belong to another driver**. The API doesn't reveal that they exist.
  - `405` for a method an endpoint doesn't support. The `Allow` header lists the ones it does.
  - `409` when a trip action breaks a rule (e.g. completing a trip that hasn't started).
  - `429` after too many login attempts (10 a minute per address by default; `LOGIN_THROTTLE_RATE` in `.env`). Wait for the `Retry-After` seconds. Behind nginx, set `DJANGO_NUM_PROXIES=1` so each client is counted separately.

### Auth & users

```
POST  /api/auth/login/                 username + password → tokens
POST  /api/auth/refresh/               refresh → new tokens
GET   /api/auth/me/                    who am I, role, what my role may see/change
POST  /api/auth/change-password/       office users
GET/PUT /api/auth/business/            business name, address, GST… (printed on reports)
GET/POST, GET/PUT/PATCH/DELETE  /api/auth/users/  /api/auth/users/<id>/     superadmin/admin only
```

### Driver API (drivers only, own records only)

```
GET  /api/driver/dashboard/            name, vehicle, current trip, month stats, unread notifications
GET  /api/driver/trips/?show=active|completed|all          GET /api/driver/trips/<id>/
POST /api/driver/trips/<id>/accept/
POST /api/driver/trips/<id>/start/     start_odometer, start_location, started_at (optional, default now)
POST /api/driver/trips/<id>/update/    trip_notes, trip_document (file)
POST /api/driver/trips/<id>/complete/  end_odometer, end_location, completed_at, trip_notes,
                                       extra_category, extra_amount, extra_receipt, trip_document
GET/POST /api/driver/fuel/             vehicle, booking, date, fuel_type, litres, rate, odometer,
                                       fuel_station, payment_method, receipt, notes
GET/POST /api/driver/expenses/         date, vehicle, booking, category, amount, description,
                                       payment_method, receipt, notes   → status "pending"
GET  /api/driver/expenses/<id>/        POST /api/driver/expenses/<id>/withdraw/   (pending only)
GET/POST /api/driver/maintenance/      vehicle, date, problem_category, priority, description, odometer, photo, notes
GET  /api/driver/maintenance/<id>/
GET  /api/driver/vehicle/              my vehicle, its documents and assignment history
GET/PATCH /api/driver/profile/         phone, email, address, blood group, emergency contact, licence, photo
POST /api/driver/profile/password/     current_password, new_password, confirm
GET  /api/driver/notifications/
GET  /api/driver/form-options/         choices for the driver's forms (own vehicle, own trips, categories…)
```

Driver answers never include booking amounts, income or profit.

### Office API (office users, by role; drivers get 403)

```
GET  /api/office/dashboard/            today on the road, needs attention, month ledger, driver numbers
GET  /api/office/resources/            definitions of every module the role may open:
                                       fields, choices, filters, totals, defaults, writable?
GET/POST            /api/office/r/<key>/          list (search, filters, ?mode=month|year|range|all,
                                                   page, page_size) with totals / add (multipart)
GET/POST/PATCH/DELETE /api/office/r/<key>/<id>/   one record / save changes (only the fields sent) / delete
                     keys: vehicles, customers, bookings, drivers, income, expenses, fuel, maintenance,
                           documents, loans, driver-payments, vehicle-types, users
GET  /api/office/expense-approvals/    pending + recently reviewed driver expenses
POST /api/office/expenses/<id>/decide/ {"action": "approve"} or {"action": "reject", "reason": "…"}
GET  /api/office/drivers/<id>/overview/?month=2026-10   profile, month numbers, salary, trips, fuel,
                                       expenses, problem reports, assignment history, activity
POST /api/office/drivers/<id>/account/ {"action": "create", "username", "password"} | "reset" | "activate" | "deactivate"
POST /api/office/drivers/<id>/assign-vehicle/   vehicle, start_date, notes
GET/POST /api/office/assign-trip/      trips without a driver + drivers / {"booking": id, "driver": id}
GET  /api/office/activity/?driver=&q=&page=
GET  /api/office/notifications/        GET /api/office/notifications/unread/
```

### Modules, finance and reports (office users, by role)

Standard REST endpoints: `GET`/`POST` on the list, and `GET`/`PUT`/`PATCH`/`DELETE` on `<id>/`.

```
/api/vehicles/   /api/vehicles/<id>/profile/        /api/vehicle-types/   /api/documents/
/api/drivers/    /api/drivers/<id>/summary/         /api/driver-payments/
/api/customers/  /api/bookings/   /api/bookings/<id>/summary/
/api/income/     /api/expenses/   /api/fuel/        /api/maintenance/
/api/loans/      /api/loans/<id>/schedule/          /api/emi/   /api/emi/<id>/pay/   /api/emi/<id>/undo/
GET /api/dashboard/          GET /api/monthly-finance/?month=&year=
GET /api/reports/            list of reports
GET /api/reports/<key>/?month=10&year=2026&vehicle=&vehicle_type=&driver=     (add &export=pdf or &export=xlsx)
```

Available reports: Income · Expense · Profit & Loss · Vehicle-wise profit · Trip-wise profit · Fuel · Maintenance · EMI · Driver salary · Customer outstanding · Document expiry · Yearly summary · Monthly driver report · Monthly vehicle report.

**Notification links** (`url` in notifications and dashboard cards) are short paths such as `/expenses/review/`, `/drivers/12/` or `/driver/trips/5/`. The app maps them to its screens (`src/lib/links.ts` in the frontend). They are built in `common/links.py`.

---

## 5. Roles

| Role | Access |
|---|---|
| **Superadmin** (`is_superuser`) | Everything, including users & roles |
| **Admin** | Everything |
| Accountant *(optional)* | Money: income, expenses, fuel, maintenance, EMI, reports |
| Manager *(optional)* | Operations: vehicles, drivers, bookings, fuel, maintenance. No profit figures |
| Staff *(optional)* | View, plus fuel entry |
| **Driver** | `/api/driver/` only: own trips, fuel, expenses, problem reports, vehicle and profile |

The table lives in `accounts/permissions.py` (`ROLE_ACCESS`). It is checked on the server for every request.

## 6. Business rules (enforced by the API)

- **Trips:** Assigned → Accepted → Ongoing (start odometer) → Completed (end odometer).
  - Total KM = end − start, and the duration is calculated from the start and end times.
  - The vehicle is *On Trip* while a trip is ongoing.
- **Fuel** from a driver counts straight away (Litres × Rate) in vehicle, trip and monthly costs.
- **Driver expenses** start **Pending**. The office approves them, or rejects them with a reason required. **Only approved expenses count** in totals, reports and profit.
- **Problem reports** go through Reported → Under Review → Approved → In Progress → Completed / Rejected. Their cost (Parts + Labour) becomes a vehicle expense **only when the status is Completed**.
- **Logins:** setting a driver to Inactive blocks their login.
- **Activity and notifications:** every important driver action goes into the activity log. Drivers and office staff get notifications.

## 7. Tests

```bat
python manage.py test
```

There are 60 tests, all through the API:
- **every endpoint for every role** (anonymous, driver, staff, manager, accountant, admin): the full permission matrix;
- **robustness:** no server error for bad queries, junk or empty bodies, malformed JSON, or missing and invalid ids; bad filters give a 400 naming the field;
- **REST basics:** create/read/update/delete round trip, pagination, `405` with `Allow`, a JSON `404` for unknown addresses, `401` for a fake token, and the login limit (`429`);
- finance calculations;
- the driver API (full trip flow, uploads, ownership);
- the office API (definitions by role, saving with side effects, approvals, accounts, files by token);
- the business and security rules above, including every report and PDF/Excel exports;
- API docs: the OpenAPI schema and the Swagger page.

## 8. Project layout

```
deploy/     DigitalOcean / VPS deployment: DEPLOY.md, systemd service, nginx config
postman/    Postman collection (smoke test + every endpoint), the OpenAPI file and the script that builds them
config/     settings, URLs (config/urls.py lists the API sections), API docs descriptions (api_docs.py)
accounts/   users, roles, login (JWT) with attempt limit, business settings
fleet/      vehicles, vehicle types, drivers, documents, assignment history
bookings/   customers and trips
finance/    income, expenses, fuel, maintenance, driver salaries, loans/EMI
reports/    reports, PDF/Excel export, dashboard figures, seed_demo command
portal/     driver API (/api/driver/): validation (forms.py), actions, activity log, notifications
office/     office API (/api/office/): dashboard, generic module list/form (resources.py), approvals, driver management
common/     dates, money, filters, query checks (params.py), notification links, protected file serving
tests/      automated tests
```
