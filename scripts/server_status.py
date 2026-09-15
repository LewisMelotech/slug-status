"""Who may say whether a script is live on the Minecraft server, and what is outstanding.

This is a deployment record, not a permission gate: every script stays visible and
downloadable here regardless of its status. All it tracks is what has actually been
put on the server, so nothing in this module filters what anyone can see.
"""

from scripts import models

SET_STATUS = "scripts.set_server_status"


def may_set_status(user):
    return bool(user and getattr(user, "is_authenticated", False) and user.has_perm(SET_STATUS))


# Everything not currently on the server is "offline", but that is two different things.
#
# A script's NEWEST version being offline is a job: either nothing of that script has ever
# been deployed, or the server is running an older one. That is the queue.
#
# An OLDER version being offline is just history. Deploying an update takes the previous
# version off the server (see ScriptVersion.save), so this pile grows by one every time
# the feature is used correctly, and never shrinks. It is worth keeping — rolling back to
# an older version is a real thing to want, and marking it online is how you do it — but
# it is not a to-do list, and it buries one if the two are shown together.
AWAITING_DEPLOYMENT = {"latest": True, "status": models.ScriptStatus.OFFLINE}
SUPERSEDED = {"latest": False, "status": models.ScriptStatus.OFFLINE}


def awaiting_deployment_count():
    """How many scripts are waiting to go on the server.

    plain_objects, not objects: the default manager annotates votes and favourites onto
    every row, and counting through that wraps the whole thing in a subquery for a number
    that does not depend on it.
    """
    return models.ScriptVersion.plain_objects.filter(**AWAITING_DEPLOYMENT).count()
