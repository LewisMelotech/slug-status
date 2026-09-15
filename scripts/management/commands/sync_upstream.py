"""Pull new versions for every script linked to an upstream instance.

Safe to run on a timer: it only ever adds versions upstream has and this instance does
not, and a script whose upstream copy has not changed costs one request.
"""

from django.core.management.base import BaseCommand, CommandError

from scripts import models, notifications, upstream


class Command(BaseCommand):
    help = "Pull new versions for scripts linked to an upstream instance."

    def add_arguments(self, parser):
        parser.add_argument(
            "--script",
            type=int,
            help="Sync only this local script id, whether or not sync is enabled for it.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be synced without writing anything.",
        )

    def handle(self, *args, **options):
        if options["script"]:
            scripts = models.Script.objects.filter(pk=options["script"])
            if not scripts:
                raise CommandError(f"No script with id {options['script']}.")
        else:
            scripts = upstream.linked_scripts()

        if not scripts:
            self.stdout.write("No linked scripts. Import one with --link first.")
            return

        # One Discord announcement for the whole run, covering every script that gained a
        # version. Hourly across a few dozen linked scripts, a message per script would be
        # a burst of near-identical pings on the hour.
        added = failures = 0
        with notifications.batched():
            for script in scripts:
                if options["dry_run"]:
                    self.stdout.write(f"would sync: {script} <- {script.upstream_url}")
                    continue
                try:
                    imported = upstream.sync_script(script)
                except upstream.UpstreamError as exc:
                    failures += 1
                    self.stderr.write(self.style.ERROR(f"{script}: {exc}"))
                    continue

                if imported:
                    added += len(imported)
                    for version in imported:
                        self.stdout.write(self.style.SUCCESS(f"new version: {script.name} {version.version}"))
                else:
                    self.stdout.write(f"up to date: {script.name}")

        if not options["dry_run"]:
            self.stdout.write(f"done: {added} new version(s) across {len(scripts)} linked script(s)")
        if failures:
            raise CommandError(f"{failures} script(s) failed to sync.")
