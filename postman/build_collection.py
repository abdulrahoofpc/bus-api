"""
Builds postman/FleetBooks-API.postman_collection.json.

    python manage.py spectacular --format openapi-json --file postman/openapi.json
    python postman/build_collection.py

Folder "1. Smoke test" runs real flows in order with pass/fail checks (Postman "Run collection",
or `npx newman run postman/FleetBooks-API.postman_collection.json`).
Folder "2. All endpoints" lists every endpoint from the OpenAPI schema, for trying by hand.
"""
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent


def script(lines):
    return [{"listen": "test", "script": {"type": "text/javascript", "exec": lines}}]


def req(name, method, path, body=None, tests=None, auth="office", query=None):
    url_path = path.lstrip("/")
    item = {
        "name": name,
        "request": {
            "method": method,
            "header": [{"key": "Accept", "value": "application/json"}],
            "url": {"raw": "{{baseUrl}}/" + url_path + (("?" + "&".join(f"{k}={v}" for k, v in query.items())) if query else ""),
                    "host": ["{{baseUrl}}"], "path": [p for p in url_path.split("/") if p] + [""],
                    **({"query": [{"key": k, "value": str(v)} for k, v in query.items()]} if query else {})},
        },
    }
    if auth == "none":
        item["request"]["auth"] = {"type": "noauth"}
    elif auth == "driver":
        item["request"]["auth"] = {"type": "bearer", "bearer": [{"key": "token", "value": "{{driverAccess}}", "type": "string"}]}
    if body is not None:
        item["request"]["header"].append({"key": "Content-Type", "value": "application/json"})
        item["request"]["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    if tests:
        item["event"] = script(tests)
    return item


def status_is(code):
    return [f'pm.test("status {code}", () => pm.response.to.have.status({code}));']


JSON_OK = ['pm.test("answer is JSON", () => pm.response.to.be.json);']

smoke = [
    req("API root", "GET", "/", auth="none", tests=status_is(200) + JSON_OK + [
        'pm.test("is FleetBooks", () => pm.expect(pm.response.json().name).to.eql("FleetBooks API"));']),
    req("Not signed in → 401", "GET", "/api/office/dashboard/", auth="none", tests=status_is(401)),
    req("Wrong password → 401", "POST", "/api/auth/login/", {"username": "{{officeUser}}", "password": "wrong-password"},
        auth="none", tests=status_is(401)),
    req("Office login", "POST", "/api/auth/login/", {"username": "{{officeUser}}", "password": "{{officePassword}}"}, auth="none",
        tests=status_is(200) + [
            "const j = pm.response.json();",
            'pm.test("got tokens", () => { pm.expect(j.access).to.be.a("string"); pm.expect(j.refresh).to.be.a("string"); });',
            'pm.collectionVariables.set("access", j.access);',
            'pm.collectionVariables.set("refresh", j.refresh);']),
    req("Refresh token", "POST", "/api/auth/refresh/", {"refresh": "{{refresh}}"}, auth="none", tests=status_is(200) + [
        "const j = pm.response.json();",
        'pm.collectionVariables.set("access", j.access); if (j.refresh) pm.collectionVariables.set("refresh", j.refresh);']),
    req("Who am I", "GET", "/api/auth/me/", tests=status_is(200) + JSON_OK),
    req("Office dashboard", "GET", "/api/office/dashboard/", tests=status_is(200) + [
        'pm.test("has board and attention", () => { const j = pm.response.json(); pm.expect(j).to.have.property("board"); pm.expect(j).to.have.property("attention"); });']),
    req("Module definitions", "GET", "/api/office/resources/", tests=status_is(200)),
    req("Vehicles (paginated)", "GET", "/api/vehicles/", tests=status_is(200) + [
        'pm.test("has results", () => pm.expect(pm.response.json().results).to.be.an("array"));']),
    req("Bad filter → 400", "GET", "/api/reports/income/", query={"month": "13"}, tests=status_is(400) + [
        'pm.test("names the field", () => pm.expect(pm.response.json()).to.have.property("month"));']),
    req("Create customer", "POST", "/api/customers/", {"name": "Postman Test Customer", "phone": "9876500000"}, tests=status_is(201) + [
        'pm.collectionVariables.set("customerId", pm.response.json().id);']),
    req("Read customer", "GET", "/api/customers/{{customerId}}/", tests=status_is(200) + [
        'pm.test("same name", () => pm.expect(pm.response.json().name).to.eql("Postman Test Customer"));']),
    req("Update customer (PATCH)", "PATCH", "/api/customers/{{customerId}}/", {"notes": "updated by Postman"}, tests=status_is(200) + [
        'pm.test("saved", () => pm.expect(pm.response.json().notes).to.eql("updated by Postman"));']),
    req("Update via office API (PATCH JSON)", "PATCH", "/api/office/r/customers/{{customerId}}/", {"company_name": "Postman Ltd"},
        tests=status_is(200) + ['pm.test("saved", () => pm.expect(pm.response.json().company_name).to.eql("Postman Ltd"));']),
    req("Delete customer", "DELETE", "/api/customers/{{customerId}}/", tests=status_is(204)),
    req("Deleted → 404", "GET", "/api/customers/{{customerId}}/", tests=status_is(404)),
    req("Report (JSON)", "GET", "/api/reports/profit-loss/", tests=status_is(200)),
    req("Report (PDF)", "GET", "/api/reports/profit-loss/", query={"export": "pdf"}, tests=status_is(200) + [
        'pm.test("is a PDF", () => pm.expect(pm.response.headers.get("Content-Type")).to.include("application/pdf"));']),
    req("Driver login", "POST", "/api/auth/login/", {"username": "{{driverUser}}", "password": "{{driverPassword}}"}, auth="none",
        tests=status_is(200) + ['pm.collectionVariables.set("driverAccess", pm.response.json().access);']),
    req("Driver home", "GET", "/api/driver/dashboard/", auth="driver", tests=status_is(200) + [
        'pm.test("no company money in driver answers", () => pm.expect(pm.response.text().toLowerCase()).to.not.include("profit"));']),
    req("Driver trips", "GET", "/api/driver/trips/", auth="driver", query={"show": "all"}, tests=status_is(200)),
    req("Driver form options", "GET", "/api/driver/form-options/", auth="driver", tests=status_is(200)),
    req("Driver blocked from office API → 403", "GET", "/api/office/dashboard/", auth="driver", tests=status_is(403)),
    req("Office user blocked from driver API → 403", "GET", "/api/driver/dashboard/", tests=status_is(403)),
    req("Unknown address → JSON 404", "GET", "/api/does-not-exist/", tests=status_is(404) + JSON_OK),
]

# ------------------------------------------------------------------ every endpoint from the schema
schema_file = HERE / "openapi.json"
groups = {}
if schema_file.exists():
    schema = json.loads(schema_file.read_text())

    def example(sch, comps, depth=0):
        if depth > 4 or not isinstance(sch, dict):
            return None
        if "$ref" in sch:
            return example(comps[sch["$ref"].split("/")[-1]], comps, depth + 1)
        if "allOf" in sch:
            return example(sch["allOf"][0], comps, depth + 1)
        if "oneOf" in sch:
            return example(sch["oneOf"][0], comps, depth + 1)
        t = sch.get("type")
        if sch.get("enum"):
            return sch["enum"][0]
        if t == "object" or "properties" in sch:
            return {k: example(v, comps, depth + 1) for k, v in sch.get("properties", {}).items()
                    if not v.get("readOnly") and v.get("format") != "binary"}
        if t == "array":
            return []
        if t == "integer":
            return 1
        if t in ("number",):
            return 0
        if t == "boolean":
            return False
        if sch.get("format") == "date":
            return "2026-10-01"
        if sch.get("format") == "date-time":
            return "2026-10-01T09:00:00"
        return ""

    comps = schema.get("components", {}).get("schemas", {})
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            tag = (op.get("tags") or ["other"])[0]
            body = None
            content = (op.get("requestBody") or {}).get("content", {})
            if "application/json" in content:
                body = example(content["application/json"].get("schema", {}), comps) or {}
            p = re.sub(r"\{(\w+)\}", r"{{\1}}", path)
            auth = "driver" if path.startswith("/api/driver/") else "none" if path in ("/api/auth/login/", "/api/auth/refresh/") else "office"
            if path == "/api/auth/login/":
                body = {"username": "{{officeUser}}", "password": "{{officePassword}}"}
            name = f"{method.upper()} {path}" + (f" — {op['summary']}" if op.get("summary") else "")
            groups.setdefault(tag, []).append(req(name[:120], method.upper(), p, body, auth=auth))

collection = {
    "info": {
        "name": "FleetBooks API",
        "_postman_id": "8c0b6f6e-5a0e-4f43-9f1b-fleetbooks0001",
        "description": "Set baseUrl (e.g. http://139.59.3.23 or http://192.168.5.28:8000) and the logins under Variables, "
                       "then run folder '1. Smoke test'. It logs in, saves the tokens and checks each answer. "
                       "Uses demo logins by default (python manage.py seed_demo); it creates and deletes one test customer.",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{access}}", "type": "string"}]},
    "variable": [
        {"key": "baseUrl", "value": "http://localhost:8000"},
        {"key": "officeUser", "value": "admin"}, {"key": "officePassword", "value": "Demo@12345"},
        {"key": "driverUser", "value": "driver"}, {"key": "driverPassword", "value": "Demo@12345"},
        {"key": "access", "value": ""}, {"key": "refresh", "value": ""}, {"key": "driverAccess", "value": ""},
        {"key": "customerId", "value": ""},
        {"key": "id", "value": "1"}, {"key": "pk", "value": "1"}, {"key": "key", "value": "vehicles"},
    ],
    "item": [
        {"name": "1. Smoke test", "description": "Run in order: logs in, checks every main flow and the security rules.", "item": smoke},
        {"name": "2. All endpoints", "description": "Every endpoint. Log in first (Smoke test → Office login / Driver login), "
                                                    "then set the id / pk / key variables.",
         "item": [{"name": tag, "item": items} for tag, items in sorted(groups.items())]},
    ],
}
out = HERE / "FleetBooks-API.postman_collection.json"
out.write_text(json.dumps(collection, indent=2))
print(f"wrote {out.name}: {len(smoke)} smoke requests, {sum(len(v) for v in groups.values())} endpoint requests")
