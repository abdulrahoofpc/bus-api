from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("customers", views.CustomerViewSet, basename="customer")
router.register("bookings", views.BookingViewSet, basename="booking")

urlpatterns = router.urls
