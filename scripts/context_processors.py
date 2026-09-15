from django.conf import settings

from scripts import server_status


def custom_configuration(request):
    return {
        "UPLOAD_DISABLED": settings.UPLOAD_DISABLED,
        "BANNER": settings.BANNER,
        "awaiting_deployment": _awaiting_deployment(request),
    }


def _awaiting_deployment(request):
    """How many scripts are waiting to go on the Minecraft server, for the nav badge.

    This runs on every render of every page, so it counts only for the few people who can
    act on it — for everyone else, which is nearly everyone and most of them anonymous, it
    must not cost a query. They never see the Server link either way.
    """
    if not server_status.may_set_status(getattr(request, "user", None)):
        return None
    return server_status.awaiting_deployment_count()
