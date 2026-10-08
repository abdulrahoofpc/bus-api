from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("vehicle-types", views.VehicleTypeViewSet)
router.register("vehicles", views.VehicleViewSet)
router.register("documents", views.VehicleDocumentViewSet)
router.register("drivers", views.DriverViewSet)

urlpatterns = router.urls
