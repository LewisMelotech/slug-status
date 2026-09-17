# Register your models here.
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
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
    list_display = ["pk", "name", "slug", "owner", "upstream_id", "sync_enabled", "last_synced"]
    list_display_links = ["pk", "name"]
    list_editable = ["slug", "sync_enabled"]
    list_filter = ["sync_enabled", "upstream_source"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["last_synced"]
    actions = ["sync_now"]

    def get_changelist_form(self, request, **kwargs):
        # ModelAdmin.form is deliberately ignored for the editable changelist,
        # so name it again here or inline slug edits would skip clean_slug.
        kwargs.setdefault("form", ScriptAdminForm)
        return super().get_changelist_form(request, **kwargs)

    @admin.action(description="Sync selected scripts from their upstream instance")
    def sync_now(self, request, queryset):
        synced = skipped = 0
        for script in queryset:
            try:
                imported = upstream.sync_script(script)
            except upstream.UpstreamError as exc:
                self.message_user(request, f"{script}: {exc}", level=messages.ERROR)
                continue
            if imported:
                synced += len(imported)
                names = ", ".join(str(version.version) for version in imported)
                self.message_user(request, f"{script}: imported {names}", level=messages.SUCCESS)
            else:
                skipped += 1
        if skipped:
            self.message_user(request, f"{skipped} script(s) were already up to date.", level=messages.INFO)
        if synced:
            self.message_user(request, f"{synced} new version(s) in total.", level=messages.SUCCESS)


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


class ScriptVersionAdmin(admin.ModelAdmin):
    readonly_fields = ["created"]
    list_display = ["pk", "script", "version", "status", "latest", "author", "created"]
    list_display_links = ["pk", "script"]
    list_editable = ["status"]
    list_filter = ["status", "latest", "script_type"]
    search_fields = ["script__name", "author"]
    actions = [mark_on_server, mark_off_server]

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
