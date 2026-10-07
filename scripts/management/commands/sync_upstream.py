"""Pull new versions of linked scripts from botcscripts.com, and keep its rows for imports.

Safe to run on a timer: it only ever adds versions botcscripts.com has and this instance
does not. It reads botcscripts.com's list of versions once, newest first, down to the
newest version seen on the last run: usually one request. Every row read is stored for
imports to use, and every new version of a linked script is added; the only other
requests are their PDFs, once each. This is the approach botcscripts.com's maintainer
asked for, and once a day is the most it should run.

`--script <id>` checks one script by itself instead, and `--full --script <id>` fetches
every version missing from it, the way a first import does. Both are for running by hand.
"""

from django.core.management.base import BaseCommand, CommandError

from scripts import models, notifications, upstream


class Command(BaseCommand):
    help = "Pull new versions of linked scripts from botcscripts.com."

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
            help="Say how far botcscripts.com would be read, without asking it anything or writing.",
        )

    def handle(self, *args, **options):
        if options["full"] and not options["script"]:
            raise CommandError("--full fetches a script's whole history, so it needs --script <id>.")
        if options["script"]:
            return self.sync_one(options["script"], full=options["full"], dry_run=options["dry_run"])

        if options["dry_run"]:
            last_seen = upstream.cursor_for()
            where = f"down to version {last_seen}" if last_seen is not None else "the newest page only"
            self.stdout.write(f"would read {upstream.SOURCE}: {where}")
            return

        # Read even with nothing linked yet: the rows it keeps are what imports are served from.
        try:
            result = upstream.sync_source()
        except upstream.UpstreamError as exc:
            raise CommandError(str(exc)) from exc

        for version in result.imported:
            self.stdout.write(self.style.SUCCESS(f"new version: {version.script.name} {version.version}"))
        for problem in result.failed:
            self.stderr.write(self.style.ERROR(problem))
        self.stdout.write(f"read {result.pages} page(s) of {upstream.SOURCE}")
        if result.first_run:
            self.stdout.write(
                "first run: read the newest page only, and later runs carry on from there. "
                "Use --full --script <id> for anything older a linked script is missing."
            )
        if result.limited:
            self.stderr.write(
                f"stopped after {upstream.MAX_PAGES} pages without reaching the last version seen, "
                "so anything older was not checked."
            )
        self.stdout.write(f"done: {len(result.imported)} new version(s)")
        if result.failed:
            raise CommandError(f"{len(result.failed)} version(s) could not be added; see above.")

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
