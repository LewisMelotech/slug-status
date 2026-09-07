from django.db import migrations


def remove_stale_permission(apps, schema_editor):
    """Drop the short-lived scripts.moderate_scripts permission.

    Django creates permissions from a model's Meta but never removes ones that stop
    being declared, so it would otherwise linger in the admin's permission picker
    looking real while nothing checks it.
    """
    Permission = apps.get_model("auth", "Permission")
    Permission.objects.filter(codename="moderate_scripts", content_type__app_label="scripts").delete()


def noop(apps, schema_editor):
    """Nothing to restore: the permission is recreated only by declaring it again."""


class Migration(migrations.Migration):
    dependencies = [
        ("scripts", "0050_scriptversion_status"),
    ]

    operations = [
        migrations.RunPython(remove_stale_permission, noop),
    ]
