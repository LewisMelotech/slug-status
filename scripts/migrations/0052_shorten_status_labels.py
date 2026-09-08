from django.db import migrations, models


class Migration(migrations.Migration):
    """Display labels only. The stored values, "offline" and "online", do not change."""

    dependencies = [
        ("scripts", "0051_remove_stale_moderate_permission"),
    ]

    operations = [
        migrations.AlterField(
            model_name="scriptversion",
            name="status",
            field=models.CharField(
                choices=[("offline", "Offline"), ("online", "Online")],
                db_index=True,
                default="offline",
                help_text="Whether this version is on the Minecraft server. Does not affect visibility here.",
                max_length=10,
            ),
        ),
    ]
