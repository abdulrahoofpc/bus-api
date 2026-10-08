from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0.00")


def D(value):
    if value is None or value == "":
        return ZERO
    return Decimal(str(value))


def money(value):
    return D(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def indian_format(value, symbol="Rs."):
    """12345678.5 -> 'Rs. 1,23,45,678.50' (Indian digit grouping)."""
    n = money(value)
    neg = n < 0
    whole, frac = f"{abs(n):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    text = f"{whole}.{frac}"
    prefix = f"{symbol} " if symbol else ""
    return f"-{prefix}{text}" if neg else f"{prefix}{text}"
