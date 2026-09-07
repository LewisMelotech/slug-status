"""Who may see an offline script, and who may put one online.

One definition, applied at every public surface. Deliberately NOT a default manager
filter: related lookups like script.versions go through the default manager, and the
upload and import paths rely on those seeing every version to decide what already
exists and which one is latest. Hiding rows there would let a second copy of an
offline version be created.
"""

from django.db.models import Q

from scripts import models

MODERATE = "scripts.moderate_scripts"


def may_moderate(user):
    return bool(user and getattr(user, "is_authenticated", False) and user.has_perm(MODERATE))


def visible_versions(queryset, user):
    """Restrict a ScriptVersion queryset to what this user may see."""
    if may_moderate(user):
        return queryset
    visible = Q(status=models.ScriptStatus.ONLINE)
    if user is not None and getattr(user, "is_authenticated", False):
        # Owners see their own while it waits, so uploading does not dead-end on a
        # page they are not allowed to open.
        visible |= Q(script__owner=user)
    return queryset.filter(visible)


def visible_scripts(queryset, user):
    """Restrict a Script queryset to those with at least one version this user may see."""
    if may_moderate(user):
        return queryset
    visible = Q(versions__status=models.ScriptStatus.ONLINE)
    if user is not None and getattr(user, "is_authenticated", False):
        visible |= Q(owner=user)
    return queryset.filter(visible).distinct()


def status_for_new_version(user):
    """A moderator's own upload skips the queue; everyone else's waits."""
    return models.ScriptStatus.ONLINE if may_moderate(user) else models.ScriptStatus.OFFLINE
