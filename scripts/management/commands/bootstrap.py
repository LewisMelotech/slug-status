"""Idempotent first-boot bootstrap: the admin account and the Discord bot's API account.

Runs on every container start, so it must be safe to repeat. `createsuperuser --noinput`
is not — it errors when the user already exists — hence this command.

Nothing here invents a password. If DJANGO_SUPERUSER_PASSWORD or BOTC_API_PASSWORD is
empty the account is skipped with an explanation, rather than being created with a
guessable default. An account that exists always keeps the password it has, unless
BOOTSTRAP_RESET_PASSWORDS=True (or --reset-passwords) is passed deliberately.
"""

import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()

# The permission scripts/viewsets.py:ScriptApiPermissions checks for every API write,
# including the slug endpoints. Declared in ScriptVersion.Meta.permissions, created by
# migration 0042, so this command must run after `migrate`.
API_WRITE_PERMISSION = "scripts.api_write_permission"


class Command(BaseCommand):
    help = "Create or refresh the admin and bot API accounts. Safe to run on every boot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Also reset the password of accounts that already exist.",
        )

    def handle(self, *args, **options):
        reset = options["reset_passwords"] or os.environ.get("BOOTSTRAP_RESET_PASSWORDS") == "True"

        self._ensure(
            username=(os.environ.get("DJANGO_SUPERUSER_USERNAME") or "").strip(),
            password=os.environ.get("DJANGO_SUPERUSER_PASSWORD") or "",
            email=(os.environ.get("DJANGO_SUPERUSER_EMAIL") or "").strip(),
            superuser=True,
            reset=reset,
            skip_hint=(
                "No superuser created: DJANGO_SUPERUSER_PASSWORD is unset. "
                "Set it in .env and restart, or create one interactively with\n"
                "  docker compose run --rm botc-scripts python manage.py createsuperuser"
            ),
        )

        self._ensure(
            username=(os.environ.get("BOTC_API_USERNAME") or "").strip(),
            password=os.environ.get("BOTC_API_PASSWORD") or "",
            email=(os.environ.get("BOTC_API_EMAIL") or "").strip(),
            permissions=(API_WRITE_PERMISSION,),
            reset=reset,
            skip_hint=(
                "No API user created: BOTC_API_PASSWORD is unset. "
                "The Discord bot's slug writes will be refused with HTTP 403 until it is set."
            ),
        )

    def _ensure(
        self,
        *,
        username,
        password,
        email="",
        superuser=False,
        permissions=(),
        reset=False,
        skip_hint="",
    ):
        if not username or not password:
            self.stdout.write(self.style.WARNING(skip_hint))
            return

        user, created = User.objects.get_or_create(
            username=username, defaults={"email": email or ""}
        )
        changed = created

        if superuser and not (user.is_superuser and user.is_staff):
            user.is_superuser = True
            user.is_staff = True
            changed = True

        if created or reset:
            user.set_password(password)
            changed = True

        if changed:
            user.save()

        for dotted in permissions:
            self._grant(user, dotted)

        if created:
            self.stdout.write(self.style.SUCCESS(f"created: {username}"))
        elif reset:
            self.stdout.write(f"exists (password reset): {username}")
        else:
            self.stdout.write(f"exists: {username}")

    def _grant(self, user, dotted):
        app_label, codename = dotted.split(".", 1)
        try:
            permission = Permission.objects.get(
                content_type__app_label=app_label, codename=codename
            )
        except Permission.DoesNotExist as exc:
            raise CommandError(
                f"Permission {dotted!r} does not exist. Run `manage.py migrate` first."
            ) from exc

        if user.user_permissions.filter(pk=permission.pk).exists():
            return
        user.user_permissions.add(permission)
        self.stdout.write(self.style.SUCCESS(f"granted {dotted} to {user.username}"))
