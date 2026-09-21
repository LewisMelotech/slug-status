from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings


class LocalAccountAdapter(DefaultAccountAdapter):
    """Lets an instance close local signup while leaving local login working."""

    def is_open_for_signup(self, request):
        return settings.LOCAL_SIGNUP_ENABLED


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Lets an instance close social signup on its own, and close local signup without it.

    allauth's default asks the account adapter, so it would follow LOCAL_SIGNUP_ENABLED
    and the two could never be set apart. Someone who already has an account can still log
    in with their provider either way: this is only asked of someone who has none.
    """

    def is_open_for_signup(self, request, sociallogin):
        return settings.SOCIAL_SIGNUP_ENABLED
