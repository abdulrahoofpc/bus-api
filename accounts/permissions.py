"""
Role-based access. Each API view declares a `module`; this table says what
each role may do there: "rw" = view + add/edit/delete, "r" = view only.
Change this table to adjust access without touching any view.
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

MODULES = [
    "dashboard", "vehicles", "vehicle_types", "documents", "drivers", "driver_payments",
    "customers", "bookings", "income", "expenses", "fuel", "maintenance",
    "finance", "reports", "settings", "users",
]

ROLE_ACCESS = {
    "admin": {m: "rw" for m in MODULES},
    "accountant": {
        "dashboard": "r", "vehicles": "r", "vehicle_types": "r", "documents": "r",
        "drivers": "r", "driver_payments": "rw", "customers": "r", "bookings": "r",
        "income": "rw", "expenses": "rw", "fuel": "rw", "maintenance": "rw",
        "finance": "rw", "reports": "r", "settings": "r",
    },
    "manager": {
        "dashboard": "r", "vehicles": "rw", "vehicle_types": "rw", "documents": "rw",
        "drivers": "rw", "driver_payments": "r", "customers": "rw", "bookings": "rw",
        "fuel": "rw", "maintenance": "rw", "settings": "r",
    },
    "staff": {
        "dashboard": "r", "vehicles": "r", "vehicle_types": "r", "documents": "r",
        "drivers": "r", "customers": "r", "bookings": "r", "fuel": "rw", "settings": "r",
    },
    # Drivers use /api/driver/ only: no office API is open to them.
    "driver": {},
}

# Roles that may see money figures on the dashboard.
FINANCE_ROLES = {"admin", "accountant"}


def access_for(user):
    if not user or not user.is_authenticated:
        return {}
    if user.is_superuser:
        return ROLE_ACCESS["admin"]
    return ROLE_ACCESS.get(user.role, {})


def can_see_finance(user):
    return bool(user and user.is_authenticated) and (user.is_superuser or user.role in FINANCE_ROLES)


class ModulePermission(BasePermission):
    message = "Your role doesn't have access to this section."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        module = getattr(view, "module", None)
        level = access_for(request.user).get(module)
        if request.method in SAFE_METHODS:
            return level in ("r", "rw")
        return level == "rw"


class NotDriver(BasePermission):
    """Default safety net for the office API: drivers use /api/driver/ only."""
    message = "Drivers can only use the Driver API (/api/driver/)."

    def has_permission(self, request, view):
        user = request.user
        return not (user and user.is_authenticated and getattr(user, "is_driver", False))
