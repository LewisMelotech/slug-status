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


# --- Splitting the offline pile ---------------------------------------------------------


class StubManager:
    """Stands in for a model manager, recording what it was asked for."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def filter(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def select_related(self, *args):
        self.calls.append({"select_related": args})
        return self

    def order_by(self, *args):
        self.calls.append({"order_by": args})
        return self

    def __iter__(self):
        return iter(self.rows)

    def count(self):
        return len(self.rows)


class StubVersion:
    def __init__(self, script_id, version="1.0.0"):
        self.script_id = script_id
        self.version = version


def test_the_two_halves_of_the_offline_pile_are_complementary():
    """Together they are every offline version, and nothing is in both."""
    from scripts.server_status import AWAITING_DEPLOYMENT, SUPERSEDED

    assert AWAITING_DEPLOYMENT["status"] == models.ScriptStatus.OFFLINE
    assert SUPERSEDED["status"] == models.ScriptStatus.OFFLINE
    assert AWAITING_DEPLOYMENT["latest"] is True
    assert SUPERSEDED["latest"] is False
    # Same keys, so neither can quietly pick up a condition the other lacks and start
    # leaving offline versions out of both tabs.
    assert AWAITING_DEPLOYMENT.keys() == SUPERSEDED.keys()


def test_the_queue_is_the_latest_version_not_every_version():
    """Regression: the page used to list every offline version.

    Superseded versions outnumbered outstanding ones 25 to 8 on the live instance, and
    they accumulate forever, so the queue has to be the latest-version half.
    """
    from scripts.server_status import AWAITING_DEPLOYMENT

    assert AWAITING_DEPLOYMENT["latest"] is True


@pytest.mark.parametrize(
    "query, expected_latest",
    [({}, True), ({"show": "superseded"}, False), ({"show": "anything else"}, True)],
)
def test_the_tab_selects_which_half_is_listed(monkeypatch, query, expected_latest):
    from scripts import views

    manager = StubManager()
    monkeypatch.setattr(views.models.ScriptVersion, "objects", manager)

    view = views.ServerQueueView()
    view.request = type("R", (), {"GET": query})()
    view.get_queryset()

    assert manager.calls[0]["latest"] is expected_latest
    assert manager.calls[0]["status"] == models.ScriptStatus.OFFLINE


def test_the_queue_puts_the_newest_arrival_first(monkeypatch):
    """A Discord announcement links straight here, so what just arrived must be on top.

    Ordered oldest-first with 25 to a page, the version someone was just told about
    landed on the last page.
    """
    from scripts import views

    manager = StubManager()
    monkeypatch.setattr(views.models.ScriptVersion, "objects", manager)

    view = views.ServerQueueView()
    view.request = type("R", (), {"GET": {}})()
    view.get_queryset()

    assert {"order_by": ("-created",)} in manager.calls


def test_each_row_says_what_is_on_the_server_instead(monkeypatch):
    from scripts import views

    online = StubVersion(script_id=1, version="1.0.4")
    monkeypatch.setattr(views.models.ScriptVersion, "plain_objects", StubManager([online]))

    waiting = StubVersion(script_id=1, version="1.0.5")
    never_deployed = StubVersion(script_id=2, version="1.0.0")
    views._attach_online_version([waiting, never_deployed])

    assert waiting.currently_online is online
    assert never_deployed.currently_online is None


def test_an_empty_page_costs_no_query(monkeypatch):
    from scripts import views

    manager = StubManager()
    monkeypatch.setattr(views.models.ScriptVersion, "plain_objects", manager)
    views._attach_online_version([])
    assert manager.calls == []


# --- The nav badge ----------------------------------------------------------------------


@pytest.fixture
def rendering(settings):
    """UPLOAD_DISABLED and BANNER are supplied by botc/docker.py, not by the base settings.

    The context processor reads both on every render, so it cannot be called without
    them — which is exactly why botc/docker.py says they are not optional.
    """
    settings.UPLOAD_DISABLED = False
    settings.BANNER = None
    return settings


def test_the_badge_is_not_counted_for_people_who_cannot_act_on_it(rendering, monkeypatch):
    """It runs on every render of every page, most of them for anonymous visitors."""
    from scripts import context_processors

    def fail():
        raise AssertionError("counted for a user who cannot set status")

    monkeypatch.setattr(context_processors.server_status, "awaiting_deployment_count", fail)

    for user in (StubAnonymous(), StubUser(), StubUser("scripts.api_write_permission"), None):
        request = type("R", (), {"user": user})()
        assert context_processors.custom_configuration(request)["awaiting_deployment"] is None


def test_the_badge_is_counted_for_someone_who_can(rendering, monkeypatch):
    from scripts import context_processors

    monkeypatch.setattr(context_processors.server_status, "awaiting_deployment_count", lambda: 8)
    request = type("R", (), {"user": StubUser("scripts.set_server_status")})()
    assert context_processors.custom_configuration(request)["awaiting_deployment"] == 8


@pytest.mark.parametrize("template", ["server_queue.html", "navbar.html"])
def test_the_templates_compile(template):
    """A broken tag in a template is a 500 on a live page, not a failing import."""
    from django.template.loader import get_template

    get_template(template)


@pytest.mark.parametrize(
    "name, args",
    [
        ("server_queue", {}),
        ("script", {"pk": 12}),
        ("set_script_status", {"pk": 34}),
        ("download_json", {"pk": 12, "version": "1.0.0"}),
        ("download_pdf", {"pk": 12, "version": "1.0.0"}),
    ],
)
def test_the_urls_the_server_page_reverses_exist(name, args):
    """{% url %} resolves at render time, so a wrong name here only fails in the browser."""
    from django.urls import reverse

    assert reverse(name, kwargs=args)
