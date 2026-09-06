# Register your models here.
from django import forms
from django.contrib import admin

from scripts import models, slugs


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
    list_display = ["pk", "name", "slug", "owner"]
    list_display_links = ["pk", "name"]
    list_editable = ["slug"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}

    def get_changelist_form(self, request, **kwargs):
        # ModelAdmin.form is deliberately ignored for the editable changelist,
        # so name it again here or inline slug edits would skip clean_slug.
        kwargs.setdefault("form", ScriptAdminForm)
        return super().get_changelist_form(request, **kwargs)


class ScriptVersionAdmin(admin.ModelAdmin):
    readonly_fields = ["created"]


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
