from django.urls import path

from . import api

app_name = "driver_api"

urlpatterns = [
    path("dashboard/", api.dashboard, name="dashboard"),
    path("trips/", api.trips, name="trips"),
    path("trips/<int:pk>/", api.trip_detail, name="trip"),
    path("trips/<int:pk>/accept/", api.trip_accept, name="trip_accept"),
    path("trips/<int:pk>/start/", api.trip_start, name="trip_start"),
    path("trips/<int:pk>/update/", api.trip_update, name="trip_update"),
    path("trips/<int:pk>/complete/", api.trip_complete, name="trip_complete"),
    path("fuel/", api.fuel, name="fuel"),
    path("expenses/", api.expenses, name="expenses"),
    path("expenses/<int:pk>/", api.expense_detail, name="expense"),
    path("expenses/<int:pk>/withdraw/", api.expense_withdraw, name="expense_withdraw"),
    path("maintenance/", api.maintenance, name="maintenance"),
    path("maintenance/<int:pk>/", api.maintenance_detail, name="maintenance_detail"),
    path("vehicle/", api.vehicle, name="vehicle"),
    path("profile/", api.profile, name="profile"),
    path("profile/password/", api.change_password, name="password"),
    path("notifications/", api.notifications, name="notifications"),
    path("form-options/", api.form_options, name="form_options"),
]
