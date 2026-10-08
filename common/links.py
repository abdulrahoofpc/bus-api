"""
Links stored in notifications and dashboard cards.

They are short paths such as "/expenses/review/" or "/drivers/12/". The mobile app
turns each one into the matching screen (see mobile/src/lib/links.ts), so keep
these paths stable: existing notifications in the database already use them.
"""


def resource_list(key, query=""):
    return f"/{key}/" + (f"?{query}" if query else "")


def resource_edit(key, pk):
    return f"/{key}/{pk}/edit/"


def vehicle(pk, query=""):
    return f"/vehicles/{pk}/" + (f"?{query}" if query else "")


def driver(pk):
    return f"/drivers/{pk}/"


def expense_review():
    return "/expenses/review/"


def finance(query=""):
    return "/finance/" + (f"?{query}" if query else "")


def assign_trip():
    return "/drivers/assign-trip/"


def reports(query=""):
    return "/reports/" + (f"?{query}" if query else "")
