"""Rupee and date formatting for choice labels and totals (Indian digit grouping)."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def _group(whole: str) -> str:
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def rupees(value, whole=False, sign=False):
    """
    ₹1,23,456 for whole amounts, ₹1,23,456.50 when there are paise (never one decimal).
    whole=True rounds to the rupee for big summary figures.
    """
    try:
        n = Decimal(str(value if value not in (None, "") else 0))
    except Exception:
        return ""
    n = n.quantize(Decimal("1") if whole else Decimal("0.01"), rounding=ROUND_HALF_UP)
    neg = n < 0
    text = f"{abs(n):.2f}" if not whole else f"{abs(n):.0f}"
    if "." in text:
        w, frac = text.split(".")
        text = _group(w) + ("" if frac == "00" else f".{frac}")
    else:
        text = _group(text)
    prefix = "−" if neg else ("+" if sign and n > 0 else "")
    return f"{prefix}₹{text}"


def number(value):
    if value in (None, ""):
        return ""
    n = Decimal(str(value))
    if n == n.to_integral():
        return _group(str(abs(int(n)))) if n >= 0 else "−" + _group(str(abs(int(n))))
    w, frac = f"{abs(n):.2f}".split(".")
    return ("−" if n < 0 else "") + _group(w) + "." + frac.rstrip("0")


def short_date(value):
    if not value:
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return value.strftime("%d %b").lstrip("0")
