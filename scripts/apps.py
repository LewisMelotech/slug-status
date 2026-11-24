from django.apps import AppConfig


class ScriptsConfig(AppConfig):
    name = "scripts"

    def ready(self):
        # Importing these modules is what connects their receivers: notifications
        # announces every new script version, and signals keeps the stored vote,
        # favourite and comment counts and the character masks up to date.
        from scripts import notifications, signals  # noqa: F401
