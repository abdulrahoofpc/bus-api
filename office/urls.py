from django.urls import path

from . import api

app_name = "office_api"

urlpatterns = [
    path("dashboard/", api.dashboard, name="dashboard"),
    path("resources/", api.resource_defs, name="resources"),
    path("r/<slug:key>/", api.resource_list, name="resource_list"),
    path("r/<slug:key>/<int:pk>/", api.resource_detail, name="resource_detail"),
    path("expense-approvals/", api.expense_approvals, name="expense_approvals"),
    path("expenses/<int:pk>/decide/", api.expense_decide, name="expense_decide"),
    path("drivers/<int:pk>/overview/", api.driver_overview_api, name="driver_overview"),
    path("drivers/<int:pk>/account/", api.driver_account, name="driver_account"),
    path("drivers/<int:pk>/assign-vehicle/", api.assign_vehicle, name="assign_vehicle"),
    path("assign-trip/", api.assign_trip, name="assign_trip"),
    path("activity/", api.activity, name="activity"),
    path("notifications/", api.notifications, name="notifications"),
    path("notifications/unread/", api.unread, name="unread"),
]
