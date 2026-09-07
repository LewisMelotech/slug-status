import pytest

from scripts import models
from scripts.server_status import may_set_status


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
        (StubUser("scripts.set_server_status"), True),
        (StubUser("scripts.api_write_permission"), False),
        (StubUser(), False),
        (StubAnonymous(), False),
        (None, False),
    ],
)
def test_may_set_status(user, expected):
    assert may_set_status(user) is expected


def test_new_versions_start_off_the_server():
    # Nothing has been deployed at the moment it is uploaded, whoever uploaded it.
    assert models.ScriptVersion._meta.get_field("status").default == models.ScriptStatus.OFFLINE


def test_status_does_not_gate_anything():
    """The status is a deployment record, not a permission.

    Guards against the module regrowing visibility helpers: a script's status must
    never decide who can see or download it.
    """
    import scripts.server_status as module

    assert not [name for name in dir(module) if "visible" in name or "hide" in name]


@pytest.mark.parametrize("value", ["offline", "online"])
def test_status_values(value):
    assert value in models.ScriptStatus.values
