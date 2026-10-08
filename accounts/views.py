from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.views import BaseViewSet

from .models import BusinessSettings, User
from .throttles import LoginRateThrottle
from .permissions import ModulePermission
from .serializers import (BusinessSettingsSerializer, ChangePasswordSerializer,
                          MeSerializer, UserSerializer)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(MeSerializer(request.user).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    ser = ChangePasswordSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    if not request.user.check_password(ser.validated_data["current_password"]):
        return Response({"current_password": ["Current password is incorrect."]}, status=400)
    request.user.set_password(ser.validated_data["new_password"])
    request.user.save()
    return Response({"detail": "Password changed."})


class UserViewSet(BaseViewSet):
    module = "users"
    queryset = User.objects.all().order_by("username")
    serializer_class = UserSerializer
    search_fields = ["username", "first_name", "last_name", "email"]

    def check_can_delete(self, instance):
        if instance == self.request.user:
            return "You can't delete your own account."
        return None


class BusinessSettingsView(APIView):
    permission_classes = [ModulePermission]
    module = "settings"

    def get(self, request):
        return Response(BusinessSettingsSerializer(BusinessSettings.load()).data)

    def put(self, request):
        ser = BusinessSettingsSerializer(BusinessSettings.load(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=status.HTTP_200_OK)


class LoginView(TokenObtainPairView):
    """POST username + password → access and refresh tokens. Limited to a few attempts per minute per address."""
    throttle_classes = [LoginRateThrottle]
