"""Who may register: one switch for username/password signup, one for social signup."""

import pytest
from allauth.account.adapter import get_adapter as get_account_adapter
from allauth.socialaccount.adapter import get_adapter as get_social_adapter
from django.test import RequestFactory, override_settings

request = RequestFactory().get("/")


@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize("social", [True, False])
def test_each_kind_of_signup_follows_only_its_own_switch(local, social):
    """All four combinations, because the two used to be one switch by accident.

    allauth's own social adapter asks the account adapter whether signup is open, so
    without an adapter of its own, closing username/password signup closed first-time
    Discord and Google signup with it. Only the mixed cases can tell the two apart.
    """
    with override_settings(LOCAL_SIGNUP_ENABLED=local, SOCIAL_SIGNUP_ENABLED=social):
        assert get_account_adapter(request).is_open_for_signup(request) is local
        assert get_social_adapter(request).is_open_for_signup(request, sociallogin=None) is social
