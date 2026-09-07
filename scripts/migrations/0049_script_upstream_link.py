from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("scripts", "0048_trigram_extension"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="script",
            name="last_synced",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="script",
            name="sync_enabled",
            field=models.BooleanField(
                default=False, help_text="Pull new versions from upstream whenever sync_upstream runs."
            ),
        ),
        migrations.AddField(
            model_name="script",
            name="upstream_id",
            field=models.IntegerField(
                blank=True,
                help_text="The script id on the upstream instance, which is unrelated to the id here.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="script",
            name="upstream_source",
            field=models.URLField(
                blank=True,
                help_text="Base URL of the instance this was imported from, e.g. https://www.botcscripts.com",
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="script",
            constraint=models.UniqueConstraint(
                condition=models.Q(("upstream_id__isnull", False)),
                fields=("upstream_source", "upstream_id"),
                name="unique_upstream_script",
            ),
        ),
    ]
