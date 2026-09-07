from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings


class LocalAccountAdapter(DefaultAccountAdapter):
    """Lets an instance close local signup while leaving local login working."""

    def is_open_for_signup(self, request):
        return settings.LOCAL_SIGNUP_ENABLED
