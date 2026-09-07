# Register your models here.
from django import forms
from django.contrib import admin, messages

from scripts import models, slugs, upstream


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
    updated = queryset.update(status=models.ScriptStatus.ONLINE)
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
