from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("monthly-finance/", views.monthly_finance, name="monthly-finance"),
    path("reports/", views.report_list, name="report-list"),
    path("reports/<slug:key>/", views.report_run, name="report-run"),
]
