"""Import a script from another botc-scripts instance, PDF and all."""

from django.core.management.base import BaseCommand, CommandError

from scripts import notifications, upstream


class Command(BaseCommand):
    help = "Import a script from another botc-scripts instance by id or URL."

    def add_arguments(self, parser):
        parser.add_argument(
            "reference",
            nargs="+",
            help="Script id or URL, e.g. 134 or https://www.botcscripts.com/script/134",
        )
        parser.add_argument(
            "--source",
            default=upstream.DEFAULT_SOURCE,
            help=f"Instance to import from when a bare id is given. Default {upstream.DEFAULT_SOURCE}.",
        )
        parser.add_argument(
            "--all-versions",
            action="store_true",
            help="Import every version rather than only the latest.",
        )
        parser.add_argument(
            "--no-link",
            action="store_true",
            help="Import once without linking, so sync_upstream will not follow it.",
        )

    def handle(self, *args, **options):
        failures = 0
        for reference in options["reference"]:
            try:
                with notifications.attributed("Imported", user=None, origin="the command line"):
                    script, imported, skipped = upstream.import_script(
                        reference,
                        source=options["source"],
                        link=not options["no_link"],
                        all_versions=options["all_versions"],
                        # Run from a shell by whoever administers the instance, not by a visitor.
                        enforce_owner=False,
                    )
            except upstream.UpstreamError as exc:
                failures += 1
                self.stderr.write(self.style.ERROR(f"{reference}: {exc}"))
                continue

            for version in imported:
                pdf = "with PDF" if version.pdf else "no PDF"
                self.stdout.write(self.style.SUCCESS(f"imported: {script.name} {version.version} ({pdf})"))
            if skipped:
                self.stdout.write(f"already held: {skipped} version(s) of {script.name}")
            if not imported and not skipped:
                self.stdout.write(f"nothing to do for {reference}")
            if script and script.sync_enabled:
                self.stdout.write(f"linked to {script.upstream_url} — sync_upstream will follow it")

        if failures:
            raise CommandError(f"{failures} of {len(options['reference'])} import(s) failed.")
