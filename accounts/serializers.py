from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import BusinessSettings, User
from .permissions import access_for, can_see_finance


class MeSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    can_see_finance = serializers.SerializerMethodField()
    display_name = serializers.CharField(read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "display_name",
                  "role", "role_label", "permissions", "can_see_finance"]

    def get_permissions(self, obj):
        return access_for(obj)

    def get_can_see_finance(self, obj):
        return can_see_finance(obj)


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "phone",
                  "role", "role_label", "is_active", "password", "last_login"]
        read_only_fields = ["last_login"]

    def validate(self, attrs):
        password = attrs.get("password")
        if not self.instance and not password:
            raise serializers.ValidationError({"password": "Set a password for the new user."})
        if password:
            validate_password(password)
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class BusinessSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessSettings
        fields = ["name", "address", "phone", "email", "gst_number"]


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField()

    def validate_new_password(self, value):
        validate_password(value)
        return value
