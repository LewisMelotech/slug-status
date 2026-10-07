"""Pull new versions for every script linked to an upstream instance.

Safe to run on a timer: it only ever adds versions upstream has and this instance does
not. Each linked script costs the source one request, for its list of version numbers,
and two more when the source's latest version is newer than the newest held here: that
version and its PDF. Versions published in between are skipped. A source that refuses a
request is not asked anything else for the rest of the run.

`--full --script <id>` fetches every version missing from one script instead, the way a
first import does. It is deliberately one script at a time.
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
            "--full",
            action="store_true",
            help="Fetch every version missing here, not only the latest. Needs --script.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be synced without writing anything.",
        )

    def handle(self, *args, **options):
        if options["full"] and not options["script"]:
            raise CommandError("--full fetches a script's whole history, so it needs --script <id>.")

        if options["script"]:
            scripts = models.Script.objects.filter(pk=options["script"])
            if not scripts:
                raise CommandError(f"No script with id {options['script']}.")
        else:
            scripts = upstream.linked_scripts()

        if not scripts:
            self.stdout.write("No linked scripts. Import one with import_script first; linking is the default.")
            return

        # One Discord announcement for the whole run, covering every script that gained a
        # version. Hourly across a few dozen linked scripts, a message per script would be
        # a burst of near-identical pings on the hour.
        added = failures = 0
        blocked, not_checked = set(), 0
        with notifications.batched():
            for script in scripts:
                if options["dry_run"]:
                    self.stdout.write(f"would sync: {script} <- {script.upstream_url}")
                    continue
                if script.upstream_source in blocked:
                    not_checked += 1
                    continue
                try:
                    imported = upstream.sync_script(script, full=options["full"])
                except upstream.Blocked as exc:
                    # Every other script from this source would be refused too, and each
                    # refused request only counts against this instance for longer.
                    blocked.add(script.upstream_source)
                    failures += 1
                    self.stderr.write(self.style.ERROR(f"{script}: {exc}"))
                    continue
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
        if not_checked:
            self.stderr.write(
                f"not checked: {not_checked} script(s) from {', '.join(sorted(blocked))}, which refused requests"
            )
        if failures:
            raise CommandError(f"{failures} script(s) failed to sync.")
