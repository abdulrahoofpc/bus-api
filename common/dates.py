import calendar
from datetime import date

from django.utils import timezone


def today():
    return timezone.localdate()


def month_bounds(year, month):
    """First and last day of a month."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def add_months(d, months):
    """Same day N months later, clamped to month end (31 Jan + 1 month -> 28/29 Feb)."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def resolve_period(params):
    """
    Reads ?month=&year= or ?date_from=&date_to= from query params.
    Defaults to the current month. Returns (start, end, label).
    """
    t = today()
    date_from = parse_date(params.get("date_from"))
    date_to = parse_date(params.get("date_to"))
    if date_from or date_to:
        start = date_from or date(t.year, 1, 1)
        end = date_to or t
        return start, end, f"{start:%d %b %Y} to {end:%d %b %Y}"
    try:
        year = int(params.get("year") or t.year)
        month = params.get("month")
        if month in (None, "", "all"):
            if params.get("year"):
                return date(year, 1, 1), date(year, 12, 31), f"Year {year}"
            month = t.month
        month = int(month)
        start, end = month_bounds(year, month)
    except (TypeError, ValueError):
        start, end = month_bounds(t.year, t.month)
    return start, end, f"{start:%B %Y}"
