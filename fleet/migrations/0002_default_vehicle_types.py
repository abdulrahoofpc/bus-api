from django.db import migrations

DEFAULT_TYPES = ["Tourist Bus", "Taxi", "Car", "Van / Traveller", "SUV", "Other"]


def create_types(apps, schema_editor):
    VehicleType = apps.get_model("fleet", "VehicleType")
    for i, name in enumerate(DEFAULT_TYPES, start=1):
        VehicleType.objects.get_or_create(name=name, defaults={"sort_order": i * 10})


class Migration(migrations.Migration):
    dependencies = [("fleet", "0001_initial")]
    operations = [migrations.RunPython(create_types, migrations.RunPython.noop)]
