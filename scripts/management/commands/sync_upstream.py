"""Pull new versions for every script linked to an upstream instance.

Safe to run on a timer: it only ever adds versions upstream has and this instance does
not. Each source is read once, through its list of the newest version of every script,
from the newest down to the newest version seen on the last run: usually one request,
and nothing else, since sync never fetches PDFs. This is the approach the botcscripts.com
maintainer asked for, and once a day is the most it should run.

`--script <id>` checks one script by itself instead, and `--full --script <id>` fetches
every version missing from it, the way a first import does. Both are for running by hand.
"""

from django.core.management.base import BaseCommand, CommandError

from scripts import models, notifications, upstream


class Command(BaseCommand):
    help = "Pull new versions for scripts linked to an upstream instance."

    def add_arguments(self, parser):
        parser.add_argument(
            "--script",
            type=int,
            help="Check only this local script id, by itself, whether or not sync is enabled for it.",
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help="Fetch every version missing here, not only the latest. Needs --script.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be read, without asking the source anything or writing.",
        )

    def handle(self, *args, **options):
        if options["full"] and not options["script"]:
            raise CommandError("--full fetches a script's whole history, so it needs --script <id>.")
        if options["script"]:
            return self.sync_one(options["script"], full=options["full"], dry_run=options["dry_run"])

        sources = upstream.linked_sources()
        if not sources:
            self.stdout.write("No linked scripts. Import one with import_script first; linking is the default.")
            return

        # One Discord announcement for the whole run, covering every script that gained a
        # version. Across a few dozen linked scripts, a message per script would be a burst
        # of near-identical pings at the same moment.
        added = failures = 0
        with notifications.batched():
            for source in sources:
                if options["dry_run"]:
                    last_seen = upstream.cursor_for(source)
                    where = f"down to version {last_seen}" if last_seen is not None else "the newest page only"
                    self.stdout.write(f"would read {source}: {where}")
                    continue
                try:
                    result = upstream.sync_source(source)
                except upstream.UpstreamError as exc:
                    failures += 1
                    self.stderr.write(self.style.ERROR(f"{source}: {exc}"))
                    continue

                added += len(result.imported)
                for version in result.imported:
                    self.stdout.write(self.style.SUCCESS(f"new version: {version.script.name} {version.version}"))
                for problem in result.failed:
                    failures += 1
                    self.stderr.write(self.style.ERROR(f"{source}: {problem}"))
                self.stdout.write(f"read {result.pages} page(s) of {source}")
                if result.first_run:
                    self.stdout.write(
                        f"first run for {source}: read its newest page only, and later runs carry on from "
                        "there. Use --full --script <id> for anything older a linked script is missing."
                    )
                if result.limited:
                    self.stderr.write(
                        f"{source}: stopped after {upstream.MAX_PAGES} pages without reaching the last version "
                        "seen, so anything older was not checked."
                    )

        if not options["dry_run"]:
            self.stdout.write(f"done: {added} new version(s)")
        if failures:
            raise CommandError(f"{failures} problem(s) syncing; see above.")

    def sync_one(self, pk, full, dry_run):
        script = models.Script.objects.filter(pk=pk).first()
        if script is None:
            raise CommandError(f"No script with id {pk}.")
        if dry_run:
            self.stdout.write(f"would sync: {script} <- {script.upstream_url}")
            return
        with notifications.batched():
            try:
                imported = upstream.sync_script(script, full=full)
            except upstream.UpstreamError as exc:
                raise CommandError(f"{script}: {exc}") from exc
        for version in imported:
            self.stdout.write(self.style.SUCCESS(f"new version: {script.name} {version.version}"))
        if not imported:
            self.stdout.write(f"up to date: {script.name}")
