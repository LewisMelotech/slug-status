import inspect

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


def test_status_labels_are_just_online_and_offline():
    assert models.ScriptStatus.OFFLINE.label == "Offline"
    assert models.ScriptStatus.ONLINE.label == "Online"


def test_the_results_table_shows_the_custom_id():
    from scripts.tables import ClocktowerTable

    assert "slug" in ClocktowerTable.base_columns
    assert ClocktowerTable.base_columns["slug"].verbose_name == "Custom id"


def test_sorting_and_paging_keep_the_default_filters():
    """Regression: a sort link is not a filter submission.

    An unticked checkbox is absent from the query string, so absence must mean off —
    but sorting and pagination also produce query strings, and treating those as an
    empty form dropped hybrid and homebrew scripts on every sort.
    """
    from django.http import QueryDict

    from scripts.views import _filter_defaults

    for query in ("", "sort=name", "page=2", "sort=-score"):
        data = _filter_defaults(QueryDict(query))
        assert data["include_hybrid"] == "True", query
        assert data["include_homebrew"] == "True", query
        assert data["latest"] == "True", query


def test_a_submitted_form_is_taken_literally():
    from django.http import QueryDict

    from scripts.views import _filter_defaults

    # filtered=1 marks a real submission, so the missing boxes mean the user turned
    # them off — including once a sort is added on top.
    for query in ("filtered=1&latest=True", "filtered=1&latest=True&sort=name"):
        data = _filter_defaults(QueryDict(query))
        assert "include_hybrid" not in data, query
        assert "include_homebrew" not in data, query


def test_only_one_version_of_a_script_can_be_online():
    """The rule lives in ScriptVersion.save(), not in a view.

    Only one version is ever actually on the Minecraft server, so putting one online
    has to take the previous one off — whichever route did it.
    """
    from scripts.models import ScriptVersion

    source = inspect.getsource(ScriptVersion.save)
    assert "ScriptStatus.ONLINE" in source
    assert "update(status=ScriptStatus.OFFLINE)" in source
    # Scoped to the one script, and never demotes the version being saved.
    assert "script_id=self.script_id" in source
    assert "exclude(pk=self.pk)" in source
