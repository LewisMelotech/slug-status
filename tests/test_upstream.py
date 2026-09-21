import pytest
from django.contrib.auth.models import AnonymousUser

from scripts.upstream import (
    DEFAULT_SOURCE,
    NotOwner,
    UpstreamClient,
    UpstreamError,
    _latest_of,
    _target_script,
    import_script,
    normalise_source,
    parse_reference,
)


class FakeResponse:
    def __init__(self, status_code=200, content=b"", url="http://example.test/x"):
        self.status_code = status_code
        self.content = content
        self.url = url


class FakeSession:
    """Stands in for a requests.Session, including its default User-Agent header."""

    def __init__(self, response=None):
        self.headers = {"User-Agent": "python-requests/2.32.3"}
        self.response = response or FakeResponse()
        self.requested = []

    def get(self, url, timeout=None, **kwargs):
        self.requested.append(url)
        return self.response


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://www.botcscripts.com", "https://www.botcscripts.com"),
        ("https://www.botcscripts.com/", "https://www.botcscripts.com"),
        ("https://www.botcscripts.com/script/134", "https://www.botcscripts.com"),
        ("www.botcscripts.com", "https://www.botcscripts.com"),
        ("http://botc-scripts:8000", "http://botc-scripts:8000"),
    ],
)
def test_normalise_source(value, expected):
    assert normalise_source(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "not a url"])
def test_normalise_source_rejects_rubbish(value):
    with pytest.raises(UpstreamError):
        normalise_source(value)


@pytest.mark.parametrize(
    "reference, expected",
    [
        ("134", (DEFAULT_SOURCE, 134)),
        ("  134  ", (DEFAULT_SOURCE, 134)),
        ("https://www.botcscripts.com/script/134", ("https://www.botcscripts.com", 134)),
        ("https://www.botcscripts.com/script/134/1.0.0", ("https://www.botcscripts.com", 134)),
        ("http://botc-scripts:8000/script/7", ("http://botc-scripts:8000", 7)),
    ],
)
def test_parse_reference(reference, expected):
    assert parse_reference(reference) == expected


@pytest.mark.parametrize("reference", ["", "nonsense", "https://www.botcscripts.com/script/"])
def test_parse_reference_rejects_input_without_an_id(reference):
    with pytest.raises(UpstreamError):
        parse_reference(reference)


def test_client_replaces_the_sessions_default_user_agent():
    # botcscripts.com answers the python-requests User-Agent with a 403 on the PDF
    # download path, and a Session always carries one, so it must be overwritten
    # rather than defaulted.
    session = FakeSession()
    client = UpstreamClient(session=session)
    assert "python-requests" not in client.session.headers["User-Agent"]


def test_pdf_returns_the_body_when_it_is_a_pdf():
    session = FakeSession(FakeResponse(200, b"%PDF-1.7 body"))
    assert UpstreamClient(session=session).pdf(134, "1.0.0") == b"%PDF-1.7 body"


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(500, b"<!DOCTYPE html><html>error</html>"),
        FakeResponse(404, b""),
        FakeResponse(200, b"<!DOCTYPE html>not a pdf"),
    ],
)
def test_pdf_returns_none_rather_than_raising_when_there_is_no_pdf(response):
    # Upstream answers a missing PDF with a 500 and an HTML page, so a non-PDF body
    # must read as "no PDF" — the JSON is still worth importing without one.
    assert UpstreamClient(session=FakeSession(response)).pdf(134, "1.0.0") is None


def test_latest_of_prefers_the_declared_latest_version():
    versions = {"1.0.0": "http://x/1", "2.0.0": "http://x/2"}
    detail = {"latest_version": "http://x/1"}
    assert _latest_of(versions, detail) == ("1.0.0", "http://x/1")


def test_latest_of_falls_back_to_the_highest_version():
    versions = {"1.0.0": "http://x/1", "11.0.0": "http://x/11", "9.0.0": "http://x/9"}
    # Highest by version ordering, not lexicographically — 11.0.0 beats 9.0.0.
    assert _latest_of(versions, {})[0] == "11.0.0"


def test_import_serializer_resolves_a_bare_id_against_the_default_source():
    from scripts.serializers import ScriptImportSerializer

    serializer = ScriptImportSerializer(data={"reference": "134"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["upstream_id"] == 134
    assert serializer.validated_data["source"] == DEFAULT_SOURCE
    # Linking is the default: an import you have to opt into following would leave
    # sync_upstream silently doing nothing.
    assert serializer.validated_data["link"] is True
    assert serializer.validated_data["all_versions"] is False


def test_import_serializer_takes_the_source_from_a_url_reference():
    from scripts.serializers import ScriptImportSerializer

    serializer = ScriptImportSerializer(data={"reference": "http://botc-scripts:8000/script/7"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["source"] == "http://botc-scripts:8000"
    assert serializer.validated_data["upstream_id"] == 7


@pytest.mark.parametrize("reference", ["nonsense", "", "https://www.botcscripts.com/script/"])
def test_import_serializer_rejects_a_reference_without_an_id(reference):
    from scripts.serializers import ScriptImportSerializer

    serializer = ScriptImportSerializer(data={"reference": reference})
    assert not serializer.is_valid()
    assert "reference" in serializer.errors


def test_import_form_resolves_a_reference_and_defaults_to_linking():
    from scripts.forms import ScriptImportForm

    form = ScriptImportForm(data={"reference": "134", "link": "on"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["upstream_id"] == 134
    assert form.cleaned_data["source"] == DEFAULT_SOURCE


def test_import_form_takes_the_source_from_a_url():
    from scripts.forms import ScriptImportForm

    # A privileged user, because that host is not one of the listed instances: this
    # is testing that a link's own host wins, not who is allowed to use it.
    form = ScriptImportForm(
        data={"reference": "http://botc-scripts:8000/script/7"},
        user=StubUser("scripts.api_write_permission"),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["source"] == "http://botc-scripts:8000"


def test_import_form_reports_an_unusable_reference_against_that_field():
    from scripts.forms import ScriptImportForm

    form = ScriptImportForm(data={"reference": "nonsense"})
    assert not form.is_valid()
    assert "reference" in form.errors


def test_import_is_a_reserved_slug():
    # /script/import would otherwise be shadowed by a script slugged "import".
    from django.core.exceptions import ValidationError

    from scripts.slugs import validate_script_slug

    with pytest.raises(ValidationError):
        validate_script_slug("import")


class StubUser:
    """A user without touching the database."""

    is_authenticated = True

    def __init__(self, *permissions):
        self.permissions = set(permissions)

    def has_perm(self, permission):
        return permission in self.permissions


def test_ordinary_users_are_held_to_the_configured_sources():
    from scripts.upstream import allowed_sources, may_import_from

    allowed = allowed_sources()[0]
    assert may_import_from(allowed, StubUser()) is True
    # The cloud metadata endpoint stands in for anything on the server's own network.
    assert may_import_from("http://169.254.169.254", StubUser()) is False
    assert may_import_from("http://botc-scripts:8000", StubUser()) is False


def test_the_write_permission_lifts_the_source_restriction():
    from scripts.upstream import may_import_from

    privileged = StubUser("scripts.api_write_permission")
    assert may_import_from("http://169.254.169.254", privileged) is True
    assert may_import_from("http://botc-scripts:8000", privileged) is True


def test_the_form_refuses_an_unlisted_host_pasted_as_a_link():
    from scripts.forms import ScriptImportForm

    # The check has to run on the resolved source: the dropdown is not the only way
    # to choose an instance, because a pasted link carries its own.
    form = ScriptImportForm(data={"reference": "http://169.254.169.254/script/1"}, user=StubUser())
    assert not form.is_valid()
    assert "can only be imported from" in str(form.errors)


def test_the_form_accepts_a_listed_host_for_an_ordinary_user():
    from scripts.forms import ScriptImportForm
    from scripts.upstream import allowed_sources

    form = ScriptImportForm(data={"reference": f"{allowed_sources()[0]}/script/134"}, user=StubUser())
    assert form.is_valid(), form.errors
    assert form.cleaned_data["upstream_id"] == 134


# --- Who may import into an existing script ---------------------------------------------


def held_script(name="Sects and Violets", owner_id=None, upstream_source=None):
    """A real Script, unsaved: enough for the ownership rule, which reads only these."""
    from scripts import models

    return models.Script(name=name, owner_id=owner_id, upstream_source=upstream_source)


class StubQuerySet:
    def __init__(self, script):
        self.script = script

    def first(self):
        return self.script


class StubManager:
    """Stands in for Script.objects: a linked script, and one found by name."""

    def __init__(self, linked=None, by_name=None):
        self.linked, self.by_name = linked, by_name

    def filter(self, **lookup):
        return StubQuerySet(self.linked if "upstream_source" in lookup else self.by_name)


class StubImporter:
    """A logged-in user, as far as the rule can tell."""

    def __init__(self, pk):
        self.pk = pk


def scripts_held(monkeypatch, **held):
    from scripts import models

    monkeypatch.setattr(models.Script, "objects", StubManager(**held))


ANONYMOUS_ONES = [None, AnonymousUser()]


def test_not_owner_is_an_upstream_error_so_existing_handlers_report_it():
    # The import page and the commands already catch UpstreamError and show its message.
    assert issubclass(NotOwner, UpstreamError)


@pytest.mark.parametrize("importer", [*ANONYMOUS_ONES, StubImporter(pk=2)])
def test_a_script_linked_to_this_source_is_open_to_anyone_who_can_import(monkeypatch, importer):
    # Found by its link, so its content is the source's own: importing again, or as
    # someone else, cannot change it into anything the source did not publish.
    linked = held_script(owner_id=1, upstream_source=DEFAULT_SOURCE)
    scripts_held(monkeypatch, linked=linked, by_name=None)

    assert _target_script(DEFAULT_SOURCE, 134, "Sects and Violets", importer) == (linked, False)


@pytest.mark.parametrize("importer", [*ANONYMOUS_ONES, StubImporter(pk=2)])
def test_a_script_with_no_owner_stays_open_as_uploads_are(monkeypatch, importer):
    unowned = held_script(owner_id=None)
    scripts_held(monkeypatch, linked=None, by_name=unowned)

    assert _target_script(DEFAULT_SOURCE, 134, "Sects and Violets", importer) == (unowned, False)


def test_its_owner_may_import_into_a_script_found_by_name(monkeypatch):
    owned = held_script(owner_id=1)
    scripts_held(monkeypatch, linked=None, by_name=owned)

    assert _target_script(DEFAULT_SOURCE, 134, "Sects and Violets", StubImporter(pk=1)) == (
        owned,
        False,
    )


@pytest.mark.parametrize("importer", [*ANONYMOUS_ONES, StubImporter(pk=2)])
def test_nobody_else_may_import_into_an_owned_script_that_only_shares_its_name(monkeypatch, importer):
    # Import matches by name when nothing is linked, and would otherwise add versions to
    # someone else's script and then link it to the source. Same rule as the upload form.
    scripts_held(monkeypatch, linked=None, by_name=held_script(owner_id=1))

    with pytest.raises(NotOwner, match="only its owner or staff can import into it"):
        _target_script(DEFAULT_SOURCE, 134, "Sects and Violets", importer)


@pytest.mark.parametrize("flag", ["is_staff", "is_superuser"])
def test_staff_and_superusers_may_import_into_a_script_someone_else_owns(monkeypatch, flag):
    # They can change any script from the admin, so refusing them here protects nothing.
    owned = held_script(owner_id=1)
    scripts_held(monkeypatch, linked=None, by_name=owned)
    importer = StubImporter(pk=2)
    setattr(importer, flag, True)

    assert _target_script(DEFAULT_SOURCE, 134, "Sects and Violets", importer) == (owned, False)


def test_being_signed_in_is_not_the_same_as_being_staff(monkeypatch):
    scripts_held(monkeypatch, linked=None, by_name=held_script(owner_id=1))
    importer = StubImporter(pk=2)
    importer.is_staff = importer.is_superuser = False

    with pytest.raises(NotOwner):
        _target_script(DEFAULT_SOURCE, 134, "Sects and Violets", importer)


def test_a_trusted_caller_is_not_held_to_the_rule(monkeypatch):
    # The command line and sync run as the operator, not as a visitor.
    owned = held_script(owner_id=1)
    scripts_held(monkeypatch, linked=None, by_name=owned)

    assert _target_script(DEFAULT_SOURCE, 134, "Sects and Violets", None, enforce_owner=False) == (
        owned,
        False,
    )


def test_a_name_nothing_holds_becomes_a_new_script(monkeypatch):
    scripts_held(monkeypatch, linked=None, by_name=None)

    script, is_new = _target_script(DEFAULT_SOURCE, 134, "Brand New", None)

    assert is_new is True
    assert script.name == "Brand New"


class RecordingClient:
    """An upstream that records what it was asked, and answers only about the script."""

    def __init__(self):
        self.asked = []

    def script(self, upstream_id):
        self.asked.append("script")
        return {"name": "Sects and Violets", "versions": {"1.0.0": "u1", "1.1.0": "u2"}}

    def version(self, url):
        self.asked.append("version")
        raise AssertionError("a refused import must not fetch a version")

    def pdf(self, upstream_id, version):
        self.asked.append("pdf")
        raise AssertionError("a refused import must not fetch a PDF")


def test_a_refused_import_costs_the_source_one_request_not_a_download_per_version(monkeypatch):
    scripts_held(monkeypatch, linked=None, by_name=held_script(owner_id=1))
    client = RecordingClient()

    with pytest.raises(NotOwner):
        import_script("134", client=client, user=None, all_versions=True)

    assert client.asked == ["script"]


def test_import_script_holds_a_caller_to_the_rule_unless_it_says_otherwise(monkeypatch):
    # Secure by default: a caller that forgets to say who is importing is anonymous.
    scripts_held(monkeypatch, linked=None, by_name=held_script(owner_id=1))

    with pytest.raises(NotOwner):
        import_script("134", client=RecordingClient())


# --- The callers that act for a visitor say who that is ---------------------------------


class SignedInUser(StubImporter):
    """Enough of a User to pass DRF's authentication and the write-permission check."""

    is_authenticated = True
    is_active = True

    def has_perm(self, perm, obj=None):
        return perm == "scripts.api_write_permission"


def test_the_api_imports_as_the_authenticated_user_and_answers_a_refusal_with_403(monkeypatch):
    from rest_framework.test import APIRequestFactory, force_authenticate

    from scripts import upstream, viewsets

    seen = {}

    def refuse(*args, **kwargs):
        seen.update(kwargs)
        raise NotOwner("'Sects and Violets' already exists here and belongs to another user.")

    monkeypatch.setattr(upstream, "import_script", refuse)
    user = SignedInUser(pk=3)
    request = APIRequestFactory().post("/api/script_ids/import/", {"reference": "134"}, format="json")
    force_authenticate(request, user=user)

    response = viewsets.ScriptViewSet.as_view({"post": "import_upstream"})(request)

    # 403, not the 400 the far side being unreachable gets: this is about who is asking.
    assert response.status_code == 403
    assert response.data == {"error": "'Sects and Violets' already exists here and belongs to another user."}
    assert seen["user"] is user
    assert "enforce_owner" not in seen


def test_the_import_page_imports_as_the_visitor(monkeypatch):
    from types import SimpleNamespace

    from django.test import RequestFactory

    from scripts import upstream, views

    seen = {}

    def nothing_new(*args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(pk=7, name="Sects and Violets", sync_enabled=False), [], 0

    monkeypatch.setattr(upstream, "import_script", nothing_new)
    view = views.ScriptImportView()
    view.request = RequestFactory().post("/script/import")
    view.request.user = StubImporter(pk=3)
    form = SimpleNamespace(
        cleaned_data={"upstream_id": 134, "source": DEFAULT_SOURCE, "link": True, "all_versions": False}
    )

    response = view.form_valid(form)

    assert response.status_code == 302
    assert seen["user"] is view.request.user
    assert "enforce_owner" not in seen
