"""Who may say whether a script is live on the Minecraft server.

This is a deployment record, not a permission gate: every script stays visible and
downloadable here regardless of its status. All it tracks is what has actually been
put on the server, so nothing in this module filters what anyone can see.
"""

SET_STATUS = "scripts.set_server_status"


def may_set_status(user):
    return bool(user and getattr(user, "is_authenticated", False) and user.has_perm(SET_STATUS))
