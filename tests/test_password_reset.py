import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.urls import NoReverseMatch, reverse


def test_reset_confirm_url_exists():
    url = reverse("password_reset_confirm", kwargs={"uidb64": "MQ", "token": "abc-def"})
    assert url == "/password/reset/MQ/abc-def/"


def test_reset_complete_url_exists():
    assert reverse("password_reset_complete") == "/password/reset/done/"


def test_there_is_no_self_service_reset_request_view():
    # Deliberate: this instance sends no email, so a "forgot password" form would only
    # ever promise a link that could not arrive.
    with pytest.raises(NoReverseMatch):
        reverse("password_reset")


def test_admins_can_generate_a_reset_link():
    user_admin = admin.site._registry[User]
    actions = [getattr(action, "__name__", action) for action in user_admin.actions]
    assert "generate_password_reset_link" in actions
