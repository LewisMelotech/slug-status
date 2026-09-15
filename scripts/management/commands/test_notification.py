"""Send a sample announcement, to prove the webhook is wired up.

Beats uploading a real script to find out whether the URL was pasted correctly.
"""

from django.core.management.base import BaseCommand, CommandError

from scripts import notifications


class Command(BaseCommand):
    help = "Post a sample announcement to the configured Discord webhook."

    def add_arguments(self, parser):
        parser.add_argument(
            "--digest",
            action="store_true",
            help="Send the multi-version form instead of the single-version one.",
        )

    def handle(self, *args, **options):
        if not notifications.webhook_url():
            raise CommandError(
                "DISCORD_WEBHOOK_URL is not set, so nothing is announced anywhere. "
                "Create a webhook in Discord under Channel -> Edit Channel -> "
                "Integrations -> Webhooks, and put its URL in the stack's .env file."
            )

        base = notifications.site_url()
        if base:
            self.stdout.write(f"Links will point at {base}")
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Neither SITE_URL nor CSRF_TRUSTED_ORIGINS is set, so announcements "
                    "will carry no link back to the script."
                )
            )

        sample = [
            notifications.Announcement(
                script_pk=0,
                name="Trouble Brewing",
                version="1.0.0",
                author="The Pandemonium Institute",
                new_script=True,
                path="/script/0",
            )
        ]
        if options["digest"]:
            sample.append(
                notifications.Announcement(
                    script_pk=1,
                    name="Sects and Violets",
                    version="2.1.0",
                    author=None,
                    new_script=False,
                    path="/script/1",
                )
            )

        if notifications.announce(sample):
            self.stdout.write(self.style.SUCCESS("Sent. Check the channel."))
        else:
            raise CommandError("The webhook did not accept it. The container log has the reason.")
