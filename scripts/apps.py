from django.apps import AppConfig


class ScriptsConfig(AppConfig):
    name = "scripts"

    def ready(self):
        # Importing the module is what connects its post_save receiver, which is how
        # every route that creates a script version comes to announce it.
        from scripts import notifications  # noqa: F401
