"""Send a sample announcement, to prove a webhook is wired up.

Beats uploading a real script, or deploying one, to find out whether the URL was pasted
correctly.

    manage.py test_notification             every webhook that is configured
    manage.py test_notification --arrivals  just arrivals
    manage.py test_notification --online    just deployments
"""

from django.core.management.base import BaseCommand, CommandError

from scripts import notifications


class Command(BaseCommand):
    help = "Post a sample announcement to the configured Discord webhooks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--arrivals",
            action="store_true",
            help="Test only the arrivals webhook (DISCORD_ARRIVALS_WEBHOOK_URL).",
        )
        parser.add_argument(
            "--online",
            action="store_true",
            help="Test only the deployment webhook (DISCORD_ONLINE_WEBHOOK_URL).",
        )
        parser.add_argument(
            "--batch",
            action="store_true",
            help="Send the several-at-once form instead of the single one.",
        )

    def handle(self, *args, **options):
        chosen = [
            webhook
            for webhook, flag in ((notifications.ARRIVALS, "arrivals"), (notifications.WENT_ONLINE, "online"))
            if options[flag]
        ]
        # Naming a webhook makes it an error for it to be unset. Naming none tests whichever
        # are configured, since each is optional and leaving one blank is a real setup.
        webhooks = chosen or [notifications.ARRIVALS, notifications.WENT_ONLINE]

        for webhook in webhooks:
            if webhook.url:
                continue
            if chosen:
                raise CommandError(
                    f"{webhook.url_setting} is not set, so nothing is announced there. "
                    "Create a webhook in Discord under Channel -> Edit Channel -> "
                    "Integrations -> Webhooks, and put its URL in the stack's .env file."
                )
            self.stdout.write(f"{webhook.url_setting} is not set — skipping the {webhook.label} webhook.")

        configured = [webhook for webhook in webhooks if webhook.url]
        if not configured:
            raise CommandError(
                "Neither DISCORD_ARRIVALS_WEBHOOK_URL nor DISCORD_ONLINE_WEBHOOK_URL is set, "
                "so nothing is announced anywhere."
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

        refused = []
        for webhook in configured:
            if webhook is notifications.WENT_ONLINE:
                sent = notifications.announce_deployments(self._sample_deployments(options["batch"]))
            else:
                sent = notifications.announce(self._sample_announcements(options["batch"]))
            if sent:
                self.stdout.write(self.style.SUCCESS(f"Sent to the {webhook.label} webhook. Check the channel."))
            else:
                refused.append(webhook.label)

        if refused:
            raise CommandError(
                f"The {' and '.join(refused)} webhook did not accept it. The container log has the reason."
            )

    @staticmethod
    def _sample_announcements(batch):
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
        if batch:
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
        return sample

    @staticmethod
    def _sample_deployments(batch):
        sample = [
            notifications.Deployment(
                script_pk=0,
                name="Trouble Brewing",
                version="1.1.0",
                path="/script/0",
                replacing="1.0.0",
                by="test_notification",
            )
        ]
        if batch:
            sample += [
                notifications.Deployment(
                    script_pk=1,
                    name="Sects and Violets",
                    version="2.1.0",
                    path="/script/1",
                    replacing=None,
                    by="test_notification",
                ),
                notifications.Deployment(
                    script_pk=2,
                    name="Bad Moon Rising",
                    version="1.0.0",
                    path="/script/2",
                    replacing="1.2.0",
                    by="test_notification",
                ),
            ]
        return sample
