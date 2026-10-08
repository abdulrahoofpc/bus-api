"""
Checks for query-string and body values that many endpoints share.

Bad values give a 400 answer naming the field (e.g. {"month": ["Use a month number from 1 to 12."]})
instead of a server error or a silently wrong result.
"""
from datetime import date

from rest_framework.exceptions import ValidationError

INT_PARAMS = ("vehicle", "vehicle_type", "driver", "customer", "booking", "page_size")


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _date(value):
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def check_query(params, ints=INT_PARAMS, dates=("date_from", "date_to", "from", "to")):
    """Validate the common filters in a query string. Raises ValidationError (→ HTTP 400)."""
    errors = {}
    month = params.get("month")
    if month not in (None, "", "all"):
        m = _int(month)
        if m is None or not 1 <= m <= 12:
            errors["month"] = ["Use a month number from 1 to 12."]
    year = params.get("year")
    if year not in (None, ""):
        y = _int(year)
        if y is None or not 1990 <= y <= 2100:
            errors["year"] = ["Use a year like 2026."]
    for name in dates:
        v = params.get(name)
        if v not in (None, "") and _date(v) is None:
            errors[name] = ["Use a date like 2026-10-01."]
    for name in ints:
        v = params.get(name)
        if v not in (None, "") and _int(v) is None:
            errors[name] = ["Must be a number (the record's id)."]
    if errors:
        raise ValidationError(errors)


def require_id(data, name, required=True):
    """An id from the request body: a whole number, or None when optional and empty. Raises ValidationError."""
    value = data.get(name)
    if value in (None, ""):
        if required:
            raise ValidationError({name: ["This field is required."]})
        return None
    n = _int(value)
    if n is None or n < 1:
        raise ValidationError({name: ["Must be a number (the record's id)."]})
    return n
