from rest_framework import serializers

from .models import EXPIRY_LABELS, Driver, Vehicle, VehicleDocument, VehicleType


class VehicleTypeSerializer(serializers.ModelSerializer):
    vehicle_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = VehicleType
        fields = ["id", "name", "sort_order", "is_active", "vehicle_count"]


class VehicleSerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)
    vehicle_type_name = serializers.CharField(source="vehicle_type.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    fuel_type_label = serializers.CharField(source="get_fuel_type_display", read_only=True)

    class Meta:
        model = Vehicle
        fields = ["id", "label", "registration_number", "short_name", "vehicle_type", "vehicle_type_name",
                  "brand", "model", "year", "seating_capacity", "fuel_type", "fuel_type_label",
                  "purchase_date", "purchase_price", "status", "status_label", "notes"]

    def validate_registration_number(self, value):
        value = value.upper().strip()
        qs = Vehicle.objects.filter(registration_number=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A vehicle with this registration number already exists.")
        return value


class VehicleDocumentSerializer(serializers.ModelSerializer):
    vehicle_label = serializers.CharField(source="vehicle.label", read_only=True)
    vehicle_type_name = serializers.CharField(source="vehicle.vehicle_type.name", read_only=True)
    doc_type_label = serializers.CharField(source="get_doc_type_display", read_only=True)
    status = serializers.CharField(read_only=True)
    status_label = serializers.SerializerMethodField()
    days_left = serializers.IntegerField(read_only=True)

    class Meta:
        model = VehicleDocument
        fields = ["id", "vehicle", "vehicle_label", "vehicle_type_name", "doc_type", "doc_type_label",
                  "document_number", "provider", "issue_date", "expiry_date", "amount", "file", "notes",
                  "status", "status_label", "days_left"]

    def get_status_label(self, obj):
        return EXPIRY_LABELS[obj.status]


class DriverSerializer(serializers.ModelSerializer):
    assigned_vehicle_label = serializers.CharField(source="assigned_vehicle.label", read_only=True, default="")
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    licence_status = serializers.CharField(read_only=True)
    licence_status_label = serializers.SerializerMethodField()
    trip_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Driver
        fields = ["id", "name", "phone", "licence_number", "licence_expiry", "licence_status",
                  "licence_status_label", "salary", "joining_date", "status", "status_label",
                  "assigned_vehicle", "assigned_vehicle_label", "trip_count", "notes", "email", "user",
                  "photo", "address", "emergency_contact_name", "emergency_contact_phone", "blood_group"]
        read_only_fields = ["user"]

    def get_licence_status_label(self, obj):
        return EXPIRY_LABELS[obj.licence_status]
