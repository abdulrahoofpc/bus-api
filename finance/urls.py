from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("income", views.IncomeViewSet)
router.register("expenses", views.ExpenseViewSet)
router.register("fuel", views.FuelEntryViewSet)
router.register("maintenance", views.MaintenanceViewSet)
router.register("driver-payments", views.DriverPaymentViewSet)
router.register("loans", views.LoanViewSet)
router.register("emi", views.InstallmentViewSet)

urlpatterns = router.urls
