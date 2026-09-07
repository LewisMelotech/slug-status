import pytest

from scripts import models
from scripts.moderation import may_moderate, status_for_new_version


class StubUser:
    is_authenticated = True

    def __init__(self, *permissions):
        self.permissions = set(permissions)

    def has_perm(self, permission):
        return permission in self.permissions


class StubAnonymous:
    is_authenticated = False

    def has_perm(self, permission):
        return False


@pytest.mark.parametrize(
    "user, expected",
    [
        (StubUser("scripts.moderate_scripts"), True),
        (StubUser("scripts.api_write_permission"), False),
        (StubUser(), False),
        (StubAnonymous(), False),
        (None, False),
    ],
)
def test_may_moderate(user, expected):
    # The API write permission deliberately does NOT confer moderation: a bot that may
    # upload is not thereby allowed to decide what the public sees.
    assert may_moderate(user) is expected


def test_uploads_wait_by_default():
    assert status_for_new_version(StubUser()) == models.ScriptStatus.OFFLINE
    assert status_for_new_version(StubAnonymous()) == models.ScriptStatus.OFFLINE
    assert status_for_new_version(None) == models.ScriptStatus.OFFLINE


def test_a_moderators_own_upload_skips_the_queue():
    assert status_for_new_version(StubUser("scripts.moderate_scripts")) == models.ScriptStatus.ONLINE


def test_offline_is_the_model_default():
    # The field default is what protects a creation path that forgets to set status.
    assert models.ScriptVersion._meta.get_field("status").default == models.ScriptStatus.OFFLINE
