from django.db import migrations, models


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
                    ("offline", "Offline — not on the Minecraft server"),
                    ("online", "Online — live on the Minecraft server"),
                ],
                db_index=True,
                default="offline",
                help_text=(
                    "Whether this version is live on the Minecraft server. Does not affect visibility here."
                ),
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
                        "set_server_status",
                        "Can mark a script as live on the Minecraft server, or take it back off.",
                    ),
                ]
            },
        ),
        # No backfill: nothing here has been put on the Minecraft server yet, so the
        # field default is the honest starting point for existing rows too.
    ]
