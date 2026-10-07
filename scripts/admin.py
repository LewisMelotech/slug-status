# Register your models here.
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.db.models import Q
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.html import format_html
from django.utils.http import urlsafe_base64_encode

from scripts import models, notifications, slugs, upstream


class ScriptAdminForm(forms.ModelForm):
    class Meta:
        model = models.Script
        fields = "__all__"

    def clean_slug(self):
        # Fold to the canonical spelling here, before the form's own uniqueness
        # check runs in _post_clean, so "Sects" is reported as a duplicate of an
        # existing "sects" rather than passing validation and then failing
        # against the unique index as a 500.
        return slugs.normalise_slug(self.cleaned_data.get("slug"))


class ScriptAdmin(admin.ModelAdmin):
    form = ScriptAdminForm
    list_display = ["pk", "name", "slug", "owner", "imported_by", "upstream_id", "sync_enabled", "last_synced"]
    list_display_links = ["pk", "name"]
    list_editable = ["slug", "sync_enabled"]
    list_filter = ["sync_enabled"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["last_synced"]
    actions = ["sync_now"]

    def get_changelist_form(self, request, **kwargs):
        # ModelAdmin.form is deliberately ignored for the editable changelist,
        # so name it again here or inline slug edits would skip clean_slug.
        kwargs.setdefault("form", ScriptAdminForm)
        return super().get_changelist_form(request, **kwargs)

    @admin.action(description="Check botcscripts.com for new versions of linked scripts")
    def sync_now(self, request, queryset):
        # The same single read of botcscripts.com's newest versions as the scheduled sync,
        # rather than a lookup per selected script: it blocks instances that iterate its API.
        # That read covers every linked script, not only the selection.
        if not any(
            script.sync_enabled and script.upstream_id and script.upstream_source == upstream.SOURCE
            for script in queryset
        ):
            self.message_user(request, "None of the selected scripts is linked with sync on.", level=messages.WARNING)
            return
        try:
            result = upstream.sync_source()
        except upstream.UpstreamError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return
        for problem in result.failed:
            self.message_user(request, problem, level=messages.ERROR)
        if result.imported:
            names = ", ".join(f"{version.script.name} {version.version}" for version in result.imported)
            self.message_user(request, f"Imported {names}.", level=messages.SUCCESS)
        else:
            self.message_user(request, "Every linked script is up to date.", level=messages.INFO)


@admin.register(models.UpstreamCursor)
class UpstreamCursorAdmin(admin.ModelAdmin):
    """How far sync has read botcscripts.com. Lower last_version_pk to make the next run look further back."""

    list_display = ["last_version_pk", "updated"]
    readonly_fields = ["updated"]


@admin.register(models.UpstreamVersion)
class UpstreamVersionAdmin(admin.ModelAdmin):
    """The versions sync and imports have kept from botcscripts.com, to look at, not edit.

    Each holds the API's own row for the version, and imports are built from it, so it is
    left exactly as botcscripts.com sent it.
    """

    list_display = ["upstream_pk", "script_id", "name", "version"]
    ordering = ["-upstream_pk"]
    # Exact matches on ids there; the admin compares integer fields as text for this.
    search_fields = ["script_id__exact", "upstream_pk__exact"]
    search_help_text = "A script id or version id on botcscripts.com."

    @admin.display(description="Name")
    def name(self, obj):
        return obj.row.get("name")

    @admin.display(description="Version")
    def version(self, obj):
        return obj.row.get("version")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.action(description="Mark selected versions as live on the Minecraft server")
def mark_on_server(modeladmin, request, queryset):
    # One at a time through save(), not queryset.update(), which bypasses it — save() is
    # what takes the script's previously online version off the server, and what tells
    # the deployment webhook. Batched so a whole selection is one Discord message.
    updated = 0
    with notifications.batched():
        for version in queryset:
            version.status = models.ScriptStatus.ONLINE
            notifications.note_changed_by(version, request.user)
            version.save(update_fields=["status"])
            updated += 1
    modeladmin.message_user(request, f"{updated} version(s) marked online.", level=messages.SUCCESS)


@admin.action(description="Mark selected versions as not on the Minecraft server")
def mark_off_server(modeladmin, request, queryset):
    updated = queryset.update(status=models.ScriptStatus.OFFLINE)
    modeladmin.message_user(request, f"{updated} version(s) marked offline.", level=messages.SUCCESS)


class HasPdfFilter(admin.SimpleListFilter):
    """Versions with or without a PDF: imported and synced ones arrive without."""

    title = "PDF"
    parameter_name = "has_pdf"

    def lookups(self, request, model_admin):
        return [("yes", "Has a PDF"), ("no", "No PDF")]

    def queryset(self, request, queryset):
        # An empty FileField is stored as "" rather than NULL, but both mean no file.
        missing = Q(pdf="") | Q(pdf__isnull=True)
        if self.value() == "yes":
            return queryset.exclude(missing)
        if self.value() == "no":
            return queryset.filter(missing)
        return queryset


class ScriptVersionAdmin(admin.ModelAdmin):
    readonly_fields = ["created"]
    list_display = ["pk", "script", "version", "status", "latest", "has_pdf", "author", "created"]
    list_display_links = ["pk", "script"]
    list_editable = ["status"]
    list_filter = ["status", "latest", HasPdfFilter, "script_type"]
    search_fields = ["script__name", "author"]
    actions = [mark_on_server, mark_off_server]

    @admin.display(boolean=True, description="PDF")
    def has_pdf(self, obj):
        return bool(obj.pdf)

    def save_model(self, request, obj, form, change):
        # Covers the change form and the inline status column on the list, so an
        # announcement from either says who marked the version online.
        notifications.note_changed_by(obj, request.user)
        super().save_model(request, obj, form, change)

    def changelist_view(self, request, extra_context=None):
        # Several status cells edited in one submit become one Discord message.
        with notifications.batched():
            return super().changelist_view(request, extra_context)


admin.site.register(models.ClocktowerCharacter)
admin.site.register(models.HomebrewCharacter)
admin.site.register(models.Translation)
admin.site.register(models.Comment)
admin.site.register(models.Collection)
admin.site.register(models.Favourite)
admin.site.register(models.Script, ScriptAdmin)
admin.site.register(models.ScriptVersion, ScriptVersionAdmin)
admin.site.register(models.ScriptTag)
admin.site.register(models.Vote)
admin.site.register(models.WorldCup)


@admin.action(description="Generate a password reset link")
def generate_password_reset_link(modeladmin, request, queryset):
    """Produce a one-time reset link for each selected user.

    This instance sends no email, so a reset cannot arrive in an inbox. The admin
    generates the link here and passes it to the person however they normally talk —
    Discord, usually. The link is signed with the user's current password hash and
    last-login time, so it stops working the moment it is used or the password changes,
    and expires on its own after PASSWORD_RESET_TIMEOUT.
    """
    for user in queryset:
        path = reverse(
            "password_reset_confirm",
            kwargs={
                "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
                "token": default_token_generator.make_token(user),
            },
        )
        link = request.build_absolute_uri(path)
        modeladmin.message_user(
            request,
            format_html(
                "Reset link for <strong>{}</strong> — single use, expires in {} days: {}",
                user.get_username(),
                settings.PASSWORD_RESET_TIMEOUT // 86400,
                link,
            ),
            level=messages.INFO,
        )


class UserAdmin(DjangoUserAdmin):
    actions = [*DjangoUserAdmin.actions, generate_password_reset_link]


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
