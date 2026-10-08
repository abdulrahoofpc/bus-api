from django.contrib import admin

from .models import Booking, Customer

admin.site.register(Customer, list_display=("name", "phone", "company_name"))
admin.site.register(Booking, list_display=("booking_number", "customer", "vehicle", "trip_date", "trip_status"))
