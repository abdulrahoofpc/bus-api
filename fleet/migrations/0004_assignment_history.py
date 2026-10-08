import datetime

from django.db import migrations


def backfill(apps, schema_editor):
    """Existing drivers with a vehicle get an open assignment row, so history starts complete."""
    Driver = apps.get_model("fleet", "Driver")
    VehicleAssignment = apps.get_model("fleet", "VehicleAssignment")
    for d in Driver.objects.filter(assigned_vehicle__isnull=False):
        if not VehicleAssignment.objects.filter(driver=d, end_date__isnull=True).exists():
            VehicleAssignment.objects.create(driver=d, vehicle_id=d.assigned_vehicle_id,
                                             start_date=d.joining_date or datetime.date.today(),
                                             notes="Existing assignment")


class Migration(migrations.Migration):
    dependencies = [("fleet", "0003_driver_email_driver_user_and_more")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
