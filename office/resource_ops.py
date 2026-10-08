"""
List / save / delete for every office resource (vehicles, bookings, income, …),
used by the generic endpoints /api/office/r/<key>/ and /api/office/r/<key>/<id>/.

Saving always goes through the module's DRF serializer, so every business rule
(balances, advance → income, no overlapping bookings, locked linked expenses,
EMI terms locked after payment…) lives in one place.
"""
from django.db.models import ProtectedError, Q, Sum

from accounts.permissions import access_for
from common.dates import today

LINK_PARAMS = ("search", "customer", "vehicle", "booking", "payment_status", "status", "driver", "trip_status")


def can(user, module, level="read"):
    access = access_for(user).get(module)
    return access == "rw" if level == "write" else access in ("r", "rw")


def viewset_for(res, request):
    vs = res.viewset()
    vs.request = request
    vs.format_kwarg = None
    vs.kwargs = {}
    return vs


def read_period(request, default="month", allow_all=True):
    """Period from the query string (?mode=month&month=9&year=2026 | year | range&from=&to= | all)."""
    t = today()
    mode = request.GET.get("mode")
    if not mode:
        mode = "all" if allow_all and any(request.GET.get(k) for k in LINK_PARAMS) else default
    try:
        month = int(request.GET.get("month") or t.month)
        year = int(request.GET.get("year") or t.year)
    except ValueError:
        month, year = t.month, t.year
    p = {"mode": mode, "month": month, "year": year, "from": request.GET.get("from", ""), "to": request.GET.get("to", "")}
    params = {}
    if mode == "month":
        params = {"month": month, "year": year}
    elif mode == "year":
        params = {"year": year}
    elif mode == "range":
        params = {"date_from": p["from"], "date_to": p["to"]}
    return p, {k: v for k, v in params.items() if v not in (None, "")}


def _filter_queryset(res, vs, request, qs, extra):
    data = request.GET.copy()
    for k in ("month", "year", "date_from", "date_to", "mode", "from", "to", "page", "page_size", "search"):
        data.pop(k, None)
    for k, v in extra.items():
        data[k] = str(v)
    fs_class = getattr(vs, "filterset_class", None)
    if not fs_class and getattr(vs, "filterset_fields", None):
        from django_filters.filterset import filterset_factory
        fs_class = filterset_factory(qs.model, fields=vs.filterset_fields)
    if fs_class:
        fs = fs_class(data, queryset=qs)
        if fs.is_valid():
            qs = fs.qs
    term = request.GET.get("search", "").strip()
    if term and getattr(vs, "search_fields", None):
        cond = Q()
        for f in vs.search_fields:
            cond |= Q(**{f"{f}__icontains": term})
        qs = qs.filter(cond)
    if res.ordering:
        qs = qs.order_by(res.ordering)
    return qs


def _errors_to_text(errors):
    if isinstance(errors, dict):
        return {k: " ".join(str(x) for x in (v if isinstance(v, list) else [v])) for k, v in errors.items()}
    return {"non_field_errors": " ".join(str(x) for x in errors)}


def filtered_rows(res, vs, request):
    """Rows with search, filters and period applied, plus raw totals."""
    period, period_params = read_period(request, res.default_period) if res.period else ({}, {})
    qs = _filter_queryset(res, vs, request, res.queryset(vs), period_params)
    qs = res.extra_filter(qs, request)
    tq = res.totals_queryset(qs)
    totals = {f: tq.aggregate(t=Sum(f))["t"] or 0 for f in getattr(vs, "sum_fields", ())}
    return qs, period, totals


def submit_resource(res, request, instance):
    """Validate and save posted data (multipart or JSON) for a resource. On edits, only the fields sent change.
    Returns (saved object or None, {field: error}, general error text)."""
    serializer_class = res.get_serializer_class()
    data = {}
    ser_fields = serializer_class().fields
    for f in res.fields:
        name, kind = f["name"], f["kind"]
        if kind == "checkbox":
            if instance is not None and name not in request.POST:
                continue
            data[name] = str(request.POST.get(name)).lower() not in ("none", "", "false", "0", "off")
        elif kind == "file":
            if name in request.FILES:
                data[name] = request.FILES[name]
            elif str(request.POST.get(f"{name}-clear")).lower() in ("1", "true", "on"):
                data[name] = None
        else:
            if instance is not None and name not in request.POST:
                continue          # field not sent: leave it as it is
            raw = request.POST.get(name)
            v = "" if raw is None else str(raw).strip()
            if v == "" and kind not in ("text", "textarea", "password"):
                sf = ser_fields.get(name)
                # empty optional text choice (e.g. blood group) → "", everything else (dates, numbers, links) → null
                v = "" if sf is not None and not sf.allow_null and getattr(sf, "allow_blank", False) else None
            data[name] = v
    data = res.prepare(data, instance, request)
    ser = serializer_class(instance, data=data, partial=instance is not None, context={"request": request})
    extra_errors = res.validate_extra(request, instance)
    before = res.snapshot(instance)
    if ser.is_valid() and not extra_errors:
        obj = ser.save()
        res.after_save(obj, request, before, instance is None)
        return obj, {}, ""
    errors = {**_errors_to_text(ser.errors if not ser.is_valid() else {}), **extra_errors}
    names = {f["name"] for f in res.fields}
    stray = [v for k, v in errors.items() if k not in names]
    return None, errors, " ".join(stray) or "Please check the highlighted fields."


def delete_resource(res, vs, request, obj):
    """Returns an error message, or None when deleted."""
    blocked = res.locked(obj) or (res.key == "users" and obj == request.user and "You can't delete your own account.") \
        or vs.check_can_delete(obj)
    if blocked:
        return blocked
    try:
        obj.delete()
    except ProtectedError:
        return vs.protected_message
    return None
