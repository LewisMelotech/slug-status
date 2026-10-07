"""A script's Minecraft customisations: set on upload or import, changed on its page."""

from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from scripts import constants, forms, models, upstream, views


class StubUser:
    is_authenticated = True

    def __init__(self, pk, is_staff=False):
        self.pk = pk
        self.is_staff = is_staff
        self.is_superuser = False


# --- The box, wherever a script comes in ------------------------------------------------------


@pytest.mark.parametrize("form_class", [forms.ScriptForm, forms.ScriptImportForm, forms.MinecraftCustomisationsForm])
def test_every_way_in_has_the_same_optional_box(form_class):
    field = form_class.base_fields["minecraft_customisations"]

    assert field.label == "Minecraft Customisations"
    assert field.required is False
    assert field.max_length == constants.MAX_MINECRAFT_CUSTOMISATIONS_LENGTH


def test_the_box_explains_itself_with_a_question_mark_tooltip(rendering):
    from django.template.loader import render_to_string

    html = render_to_string(
        "minecraft_customisations.html", {"field": forms.ScriptImportForm()["minecraft_customisations"]}
    )

    assert "Minecraft Customisations" in html
    assert 'data-toggle="tooltip"' in html
    assert 'title="e.g. script colour"' in html
    assert ">?</span>" in html


def test_every_page_switches_bootstrap_tooltips_on():
    from pathlib import Path

    base = (Path(views.__file__).parent / "templates" / "base.html").read_text()

    assert "$('[data-toggle=\"tooltip\"]').tooltip()" in base


@pytest.fixture
def rendering(settings):
    settings.UPLOAD_DISABLED = False
    settings.BANNER = None
    return settings


# --- Changing them on the script page ---------------------------------------------------------


@pytest.fixture
def change(monkeypatch):
    """POST to the view as ``user``, for a script stubbed in place of the database."""
    state = SimpleNamespace(saved=None, said=[])
    script = models.Script(pk=12, name="Sects and Violets", owner_id=1, minecraft_customisations="red")

    def save(update_fields=None):
        state.saved = update_fields

    script.save = save
    state.script = script
    monkeypatch.setattr(views, "get_object_or_404", lambda *args, **kwargs: state.script)

    def post(user, value):
        request = RequestFactory().post(
            "/script/12/minecraft", {"minecraft_customisations": value, "next": "/script/12"}
        )
        request.user = user
        request._messages = SimpleNamespace(add=lambda level, message, extra_tags="": state.said.append(message))
        return views.set_minecraft_customisations(request, pk=12)

    state.post = post
    return state


def test_the_owner_changes_them(change):
    response = change.post(StubUser(1), "  blue, with a gold border  ")

    assert response.status_code == 302 and response.url == "/script/12"
    assert change.script.minecraft_customisations == "blue, with a gold border"
    assert change.saved == ["minecraft_customisations"]
    assert change.said == ["Minecraft customisations saved for Sects and Violets."]


def test_staff_can_clear_them_on_a_script_with_no_owner(change):
    change.script.owner_id = None

    change.post(StubUser(2, is_staff=True), "")

    assert change.script.minecraft_customisations == ""
    assert change.said == ["Minecraft customisations cleared for Sects and Violets."]


def test_anyone_else_is_refused(change):
    with pytest.raises(PermissionDenied):
        change.post(StubUser(2), "green")

    assert change.script.minecraft_customisations == "red"
    assert change.saved is None


def test_too_long_is_refused_with_a_message(change):
    change.post(StubUser(1), "x" * (constants.MAX_MINECRAFT_CUSTOMISATIONS_LENGTH + 1))

    assert change.saved is None
    assert change.script.minecraft_customisations == "red"
    assert "at most 500 characters" in change.said[0]


def test_only_a_post_is_accepted():
    request = RequestFactory().get("/script/12/minecraft")

    assert views.set_minecraft_customisations(request, pk=12).status_code == 405


def test_the_route_is_not_mistaken_for_a_version():
    from django.urls import resolve, reverse

    path = reverse("set_minecraft_customisations", kwargs={"pk": 12})

    assert path == "/script/12/minecraft"
    assert resolve(path).func is views.set_minecraft_customisations


# --- Setting them on import ------------------------------------------------------------------


class ImportedScript(SimpleNamespace):
    """What import_script hands back, with the rule and the save the view uses."""

    def may_manage(self, user):
        return getattr(user, "is_staff", False) or (self.owner_id is not None and self.owner_id == user.pk)

    def save(self, update_fields=None):
        self.saved = update_fields


def _import_page(monkeypatch, user, value, script, imported):
    monkeypatch.setattr(upstream, "import_script", lambda *args, **kwargs: (script, imported, 0))
    view = views.ScriptImportView()
    view.request = RequestFactory().post("/script/import")
    view.request.user = user
    said = []
    view.request._messages = SimpleNamespace(add=lambda level, message, extra_tags="": said.append(message))
    form = SimpleNamespace(cleaned_data={"upstream_id": 134, "link": True, "minecraft_customisations": value})
    view.form_valid(form)
    return said


def _imported(existing_versions, owner_id=None, customisations=""):
    return ImportedScript(
        pk=7,
        name="Sects and Violets",
        owner_id=owner_id,
        sync_enabled=False,
        minecraft_customisations=customisations,
        versions=SimpleNamespace(count=lambda: existing_versions),
        saved=None,
    )


def test_an_import_that_creates_the_script_sets_them_for_whoever_imported(monkeypatch):
    script = _imported(existing_versions=2)

    _import_page(monkeypatch, StubUser(5), "purple", script, imported=[SimpleNamespace(version="1.0.0")] * 2)

    assert script.minecraft_customisations == "purple"
    assert script.saved == ["minecraft_customisations"]


def test_an_import_into_someone_elses_script_leaves_them_and_says_so(monkeypatch):
    # The script was already here with an older version, and is owned by someone else.
    script = _imported(existing_versions=2, owner_id=1, customisations="red")

    said = _import_page(monkeypatch, StubUser(5), "purple", script, imported=[SimpleNamespace(version="1.1.0")])

    assert script.minecraft_customisations == "red"
    assert script.saved is None
    assert any("left as they were" in message for message in said)


def test_an_empty_box_on_import_changes_nothing(monkeypatch):
    script = _imported(existing_versions=1, customisations="red")

    _import_page(monkeypatch, StubUser(5, is_staff=True), "", script, imported=[SimpleNamespace(version="1.0.0")])

    assert script.minecraft_customisations == "red"
    assert script.saved is None


# --- Shown to moderators on the Server page ----------------------------------------------------


def test_the_server_page_shows_each_scripts_customisations(rendering):
    from tests.test_server_status import _queue_row, _render_queue

    styled = _queue_row(7, "Assigned Mutant at Birth", "1.0.6")
    styled.script.minecraft_customisations = "teal\nno music"
    plain = _queue_row(8, "Trouble Brewing", "1.0.0")

    html = _render_queue([styled, plain])

    assert "Minecraft customisations" in html
    assert "teal<br>no music" in html
    assert "—" in html


# --- Who sees the box when adding a version -------------------------------------------------


@pytest.mark.parametrize(
    "user, shown",
    [
        (None, False),  # not signed in: the live check that found this was an anonymous visitor
        (StubUser(5), False),
        (StubUser(1), True),  # the owner
        (StubUser(5, is_staff=True), True),
    ],
)
def test_adding_a_version_shows_the_box_only_to_who_may_manage_the_script(monkeypatch, user, shown):
    from django.contrib.auth.models import AnonymousUser

    existing = SimpleNamespace(
        pk=12,
        name="Sects and Violets",
        owner=None,
        minecraft_customisations="green",
        latest_version=lambda: SimpleNamespace(
            author="Someone", version="1.0.0", notes="", tags=SimpleNamespace(all=list)
        ),
        may_manage=lambda who: getattr(who, "is_staff", False) or getattr(who, "pk", None) == 1,
    )
    monkeypatch.setattr(models.Script, "objects", SimpleNamespace(get=lambda **lookup: existing))
    view = views.ScriptUploadView()
    view.request = RequestFactory().get("/script/upload", {"script": 12})
    view.request.user = user or AnonymousUser()

    form = view.get_form()

    assert ("minecraft_customisations" in form.fields) is shown
    if shown:
        assert form.initial["minecraft_customisations"] == "green"


# --- Who imported a script -------------------------------------------------------------------


@pytest.mark.parametrize(
    "owner_id, imported_by_id, user, allowed",
    [
        (None, 5, StubUser(5), True),  # the importer
        (None, 5, StubUser(6), False),  # anyone else
        (1, 5, StubUser(5), True),  # an owner set later does not shut the importer out
        (1, 5, StubUser(1), True),
        (None, None, StubUser(5), False),  # imported anonymously: staff's alone
        (None, None, StubUser(5, is_staff=True), True),
    ],
)
def test_whoever_imported_a_script_may_look_after_it(owner_id, imported_by_id, user, allowed):
    script = models.Script(name="Sects and Violets", owner_id=owner_id, imported_by_id=imported_by_id)

    assert script.may_manage(user) is allowed


class _Stop(Exception):
    pass


def _importing(monkeypatch, user, is_new=True):
    """Run import_version as far as recording the check, and return the script it built."""
    seen = {}
    target = models.Script(name="Sects and Violets", imported_by_id=None if is_new else 9)
    monkeypatch.setattr(upstream, "_target_script", lambda *args, **kwargs: (target, is_new))

    def record(script, upstream_id, link):
        seen["script"] = script
        raise _Stop

    monkeypatch.setattr(upstream, "_record_check", record)
    # Past its transaction.atomic, which would need a database these tests do not have.
    with pytest.raises(_Stop):
        upstream.import_version.__wrapped__(
            {"script_id": 134, "name": "Sects and Violets", "version": "1.0.0"}, user=user
        )
    return seen["script"]


def test_an_import_records_who_created_the_script(monkeypatch):
    from django.contrib.auth.models import User

    importer = User(pk=5, username="importer")

    assert _importing(monkeypatch, importer).imported_by == importer


@pytest.mark.parametrize("who", ["anonymous", "nobody"])
def test_an_anonymous_import_or_a_sync_records_nobody(monkeypatch, who):
    from django.contrib.auth.models import AnonymousUser

    # Sync and the command line import with no user at all.
    user = AnonymousUser() if who == "anonymous" else None

    assert _importing(monkeypatch, user).imported_by_id is None


def test_importing_into_a_script_already_here_keeps_its_record(monkeypatch):
    from django.contrib.auth.models import User

    script = _importing(monkeypatch, User(pk=5, username="latecomer"), is_new=False)

    assert script.imported_by_id == 9


def test_the_server_page_says_who_imported_a_script_with_no_owner(rendering):
    from django.contrib.auth.models import User

    from tests.test_server_status import _queue_row, _render_queue

    imported = _queue_row(7, "Assigned Mutant at Birth", "1.0.6")
    imported.script.imported_by = User(pk=5, username="importer")

    html = _render_queue([imported, _queue_row(8, "Trouble Brewing", "1.0.0")])

    assert 'importer <span class="text-muted">(imported)</span>' in html
    assert "anonymous" in html
