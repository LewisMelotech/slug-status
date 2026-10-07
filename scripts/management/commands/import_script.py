"""Import a script from botcscripts.com: every version, PDFs and all."""

from django.core.management.base import BaseCommand, CommandError

from scripts import notifications, upstream


class Command(BaseCommand):
    help = "Import a script from botcscripts.com by id or link."

    def add_arguments(self, parser):
        parser.add_argument(
            "reference",
            nargs="+",
            help="Script id or URL, e.g. 134 or https://www.botcscripts.com/script/134",
        )
        parser.add_argument(
            "--no-link",
            action="store_true",
            help="Import once without linking, so sync_upstream will not follow it.",
        )

    def handle(self, *args, **options):
        failures = 0
        for position, reference in enumerate(options["reference"]):
            try:
                with notifications.attributed("Imported", user=None, origin="the command line"):
                    script, imported, skipped = upstream.import_script(
                        reference,
                        link=not options["no_link"],
                        # Run from a shell by whoever administers the instance, not by a visitor.
                        enforce_owner=False,
                    )
            except upstream.Blocked as exc:
                # The rest would be refused as well, and each refusal only counts against us.
                failures += len(options["reference"]) - position
                self.stderr.write(self.style.ERROR(f"{reference}: {exc}"))
                if position + 1 < len(options["reference"]):
                    self.stderr.write(f"not attempted: {', '.join(options['reference'][position + 1 :])}")
                break
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
