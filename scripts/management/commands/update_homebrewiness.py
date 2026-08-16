from django.core.management.base import BaseCommand

from scripts.models import Homebrewiness, ScriptTag, ScriptVersion
from scripts.views import create_characters_and_determine_homebrew_status

HYBRID_TAG_ID = 49
HOMEBREW_TAG_ID = 50


class Command(BaseCommand):
    help = "Update homebrewiness field and Hybrid/Homebrew tags for all script versions"

    def handle(self, *args, **options):
        scripts = ScriptVersion.objects.all()
        total = scripts.count()
        updated_count = 0
        tag_updated_count = 0
        self.stdout.write(f"Processing {total} script versions...")

        hybrid_tag = ScriptTag.objects.filter(pk=HYBRID_TAG_ID).first()
        homebrew_tag = ScriptTag.objects.filter(pk=HOMEBREW_TAG_ID).first()

        for i, script_version in enumerate(scripts, 1):
            old_homebrewiness = script_version.homebrewiness

            # Calculate the new homebrewiness value
            new_homebrewiness = create_characters_and_determine_homebrew_status(
                script_version.content, script_version.script
            )

            if old_homebrewiness != new_homebrewiness:
                updated_count += 1
                script_version.homebrewiness = new_homebrewiness
                script_version.save(update_fields=["homebrewiness"])

                self.stdout.write(
                    f"  [{i}/{total}] {script_version.script.name} v{script_version.version}: "
                    f"{old_homebrewiness} -> {new_homebrewiness}"
                )

            # Sync the Hybrid/Homebrew tags to match the (possibly updated) homebrewiness status.
            tags_changed = False
            if hybrid_tag:
                if new_homebrewiness == Homebrewiness.HYBRID:
                    if not script_version.tags.filter(pk=HYBRID_TAG_ID).exists():
                        script_version.tags.add(hybrid_tag)
                        tags_changed = True
                elif script_version.tags.filter(pk=HYBRID_TAG_ID).exists():
                    script_version.tags.remove(hybrid_tag)
                    tags_changed = True

            if homebrew_tag:
                if new_homebrewiness == Homebrewiness.HOMEBREW:
                    if not script_version.tags.filter(pk=HOMEBREW_TAG_ID).exists():
                        script_version.tags.add(homebrew_tag)
                        tags_changed = True
                elif script_version.tags.filter(pk=HOMEBREW_TAG_ID).exists():
                    script_version.tags.remove(homebrew_tag)
                    tags_changed = True

            if tags_changed:
                tag_updated_count += 1
                self.stdout.write(
                    f"  [{i}/{total}] {script_version.script.name} v{script_version.version}: tags updated"
                )

            if i % 100 == 0:
                self.stdout.write(f"Progress: {i}/{total} ({updated_count} homebrewiness updates)")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSuccessfully updated {updated_count} script versions' homebrewiness "
                f"and {tag_updated_count} script versions' tags"
            )
        )
