from django.contrib import admin

from .models import Driver, Vehicle, VehicleDocument, VehicleType

admin.site.register(VehicleType)
admin.site.register(Vehicle, list_display=("registration_number", "short_name", "vehicle_type", "status"))
admin.site.register(VehicleDocument, list_display=("vehicle", "doc_type", "expiry_date"))
admin.site.register(Driver, list_display=("name", "phone", "status", "assigned_vehicle"))
