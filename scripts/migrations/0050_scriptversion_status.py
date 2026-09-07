from django.db import migrations, models


def publish_existing_versions(apps, schema_editor):
    """Everything already here predates moderation, so it stays visible.

    Without this the field default would take every existing script offline the
    moment this migration ran, which on an instance in use looks like data loss.
    """
    ScriptVersion = apps.get_model("scripts", "ScriptVersion")
    ScriptVersion.objects.update(status="online")


def unpublish_all_versions(apps, schema_editor):
    """Reversing cannot know which were online before, so return to the default."""
    ScriptVersion = apps.get_model("scripts", "ScriptVersion")
    ScriptVersion.objects.update(status="offline")


class Migration(migrations.Migration):
    dependencies = [
        ("scripts", "0049_script_upstream_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="scriptversion",
            name="status",
            field=models.CharField(
                choices=[
                    ("offline", "Offline — awaiting review"),
                    ("online", "Online — visible to everyone"),
                ],
                db_index=True,
                default="offline",
                help_text="Offline versions are hidden from everyone but their owner and moderators.",
                max_length=10,
            ),
        ),
        migrations.AlterModelOptions(
            name="scriptversion",
            options={
                "permissions": [
                    (
                        "download_unsupported_json",
                        "Can the request the download of a JSON that replaces unsupported characters",
                    ),
                    (
                        "api_write_permission",
                        "Can create, update or delete scripts via the API. This is not required for reading scripts.",
                    ),
                    (
                        "moderate_scripts",
                        "Can put uploaded scripts online, and see offline ones. Uploads by this user skip the queue.",
                    ),
                ]
            },
        ),
        migrations.RunPython(publish_existing_versions, unpublish_all_versions),
    ]
