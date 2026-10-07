import pytest
from django.contrib.auth.models import AnonymousUser

from scripts.upstream import (
    DEFAULT_SOURCE,
    Blocked,
    NotOwner,
    UpstreamClient,
    UpstreamError,
    _latest_of,
    _select_versions,
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


@pytest.mark.parametrize("code", [403, 429])
def test_a_refusal_from_the_source_is_blocked_not_a_passing_error(code):
    # botcscripts.com's firewall answers an instance that has asked too much this way,
    # and the commands stop asking it on a Blocked rather than moving to the next script.
    client = UpstreamClient(session=FakeSession(FakeResponse(code, b"<html>The request is blocked.</html>")))

    with pytest.raises(Blocked, match=f"HTTP {code}"):
        client.script(134)


@pytest.mark.parametrize("code", [403, 429])
def test_a_refused_pdf_raises_rather_than_reading_as_no_pdf(code):
    # Read as "no PDF", a version would be stored without the PDF it has, and sync,
    # which only fetches versions it does not hold, would never go back for it.
    client = UpstreamClient(session=FakeSession(FakeResponse(code, b"<html>blocked</html>")))

    with pytest.raises(Blocked):
        client.pdf(134, "1.0.0")


def test_blocked_is_an_upstream_error_so_the_page_and_api_report_it():
    assert issubclass(Blocked, UpstreamError)


# --- Which versions to fetch --------------------------------------------------------------

OFFERED = {"1.10.0": "u10", "1.0.0": "u0", "1.9.0": "u9"}


def test_with_nothing_held_the_whole_history_is_wanted_oldest_first():
    # By version ordering, not as given and not lexicographically: 1.10.0 is newest.
    assert _select_versions(OFFERED, []) == ([("1.0.0", "u0"), ("1.9.0", "u9"), ("1.10.0", "u10")], 0)


def test_versions_already_held_are_not_fetched_again():
    assert _select_versions(OFFERED, ["1.0.0", "1.9.0"]) == ([("1.10.0", "u10")], 2)


def test_held_versions_compare_by_value_not_spelling():
    assert _select_versions({"1.0": "u"}, ["1.0.0"]) == ([], 1)


def test_latest_only_skips_the_versions_published_in_between():
    # Three releases since the last sync, and only the newest is fetched: the two between
    # are what a full sync is for.
    offered = {**OFFERED, "2.0.0": "u20"}
    assert _select_versions(offered, ["1.0.0"], latest="2.0.0") == ([("2.0.0", "u20")], 1)


def test_latest_only_wants_nothing_when_the_newest_is_already_held():
    # 1.9.0 is missing here, but the latest is held: filling that gap is a full sync's job.
    assert _select_versions(OFFERED, ["1.0.0", "1.10.0"], latest="1.10.0") == ([], 2)
    assert _select_versions(OFFERED, ["1.0.0", "1.10.0"]) == ([("1.9.0", "u9")], 2)


def test_latest_only_wants_nothing_older_than_the_newest_held():
    # Flagged latest there, but this instance already has something newer.
    assert _select_versions(OFFERED, ["1.10.0"], latest="1.9.0") == ([], 1)


def test_latest_only_with_nothing_held_takes_just_the_latest():
    assert _select_versions(OFFERED, [], latest="1.10.0") == ([("1.10.0", "u10")], 0)


def test_latest_of_prefers_the_version_the_source_flags():
    assert _latest_of(OFFERED, declared_url="u9") == "1.9.0"


def test_latest_of_falls_back_to_the_highest_version():
    # Highest by version ordering, not lexicographically: 1.10.0 beats 1.9.0.
    assert _latest_of(OFFERED) == "1.10.0"
    assert _latest_of(OFFERED, declared_url="not one of them") == "1.10.0"


def test_a_version_number_this_instance_cannot_store_is_an_upstream_error():
    with pytest.raises(UpstreamError, match="not a version number"):
        _select_versions({"v2 beta": "u"}, [])


def test_import_serializer_resolves_a_bare_id_against_the_default_source():
    from scripts.serializers import ScriptImportSerializer

    serializer = ScriptImportSerializer(data={"reference": "134"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["upstream_id"] == 134
    assert serializer.validated_data["source"] == DEFAULT_SOURCE
    # Linking is the default: an import you have to opt into following would leave
    # sync_upstream silently doing nothing.
    assert serializer.validated_data["link"] is True


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
        import_script("134", client=client, user=None)

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
    is_staff = False

    def has_perm(self, perm, obj=None):
        return perm == "scripts.api_write_permission"


def test_the_api_imports_as_the_authenticated_user_and_answers_a_refusal_with_403(monkeypatch, settings):
    from rest_framework.test import APIRequestFactory, force_authenticate

    from scripts import upstream, viewsets

    seen = {}

    def refuse(*args, **kwargs):
        seen.update(kwargs)
        raise NotOwner("'Sects and Violets' already exists here and belongs to another user.")

    monkeypatch.setattr(upstream, "import_script", refuse)
    settings.UPLOAD_DISABLED = False
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
    form = SimpleNamespace(cleaned_data={"upstream_id": 134, "source": DEFAULT_SOURCE, "link": True})

    response = view.form_valid(form)

    assert response.status_code == 302
    assert seen["user"] is view.request.user
    assert "enforce_owner" not in seen


# --- Importing follows the upload switch ------------------------------------------------


def _api_import(user):
    from rest_framework.test import APIRequestFactory, force_authenticate

    from scripts import viewsets

    request = APIRequestFactory().post("/api/script_ids/import/", {"reference": "134"}, format="json")
    force_authenticate(request, user=user)
    return viewsets.ScriptViewSet.as_view({"post": "import_upstream"})(request)


def test_the_api_refuses_an_import_while_uploads_are_disabled(monkeypatch, settings):
    from scripts import upstream

    def must_not_run(*args, **kwargs):
        raise AssertionError("an import ran while uploads were disabled")

    monkeypatch.setattr(upstream, "import_script", must_not_run)
    settings.UPLOAD_DISABLED = True

    response = _api_import(SignedInUser(pk=3))

    assert response.status_code == 403
    assert response.data == {"error": "Uploads are currently disabled."}


def test_staff_may_still_import_through_the_api_while_uploads_are_disabled(monkeypatch, settings):
    from scripts import upstream

    seen = {}

    def refuse(*args, **kwargs):
        seen.update(kwargs)
        raise NotOwner("refused for another reason")

    monkeypatch.setattr(upstream, "import_script", refuse)
    settings.UPLOAD_DISABLED = True
    staff = SignedInUser(pk=1)
    staff.is_staff = True

    _api_import(staff)

    assert seen["user"] is staff


def test_the_import_page_refuses_while_uploads_are_disabled(settings):
    from django.core.exceptions import PermissionDenied
    from django.test import RequestFactory

    from scripts import views

    settings.UPLOAD_DISABLED = True
    request = RequestFactory().post("/script/import", {"reference": "134"})
    request.user = SignedInUser(pk=3)

    with pytest.raises(PermissionDenied):
        views.ScriptImportView.as_view()(request)


# --- What an import, or a check of one script, costs the source ------------------------


class ServingClient:
    """An upstream holding one script, recording every request made of it."""

    def __init__(self, *numbers, flagged=None):
        self.numbers = list(numbers)
        self.flagged = flagged
        self.asked = []

    def _url(self, number):
        return f"/api/scripts/{self.numbers.index(number)}/"

    def script(self, upstream_id):
        self.asked.append("script")
        return {
            "name": "Sects and Violets",
            "versions": {number: self._url(number) for number in self.numbers},
            "latest_version": self._url(self.flagged) if self.flagged else None,
        }

    def version(self, url):
        number = self.numbers[int(url.strip("/").rsplit("/", 1)[-1])]
        self.asked.append(f"version {number}")
        return {"script_id": 134, "name": "Sects and Violets", "version": number, "content": []}

    def pdf(self, upstream_id, version):
        # No PDF: what is under test is that it was asked for, not what came back.
        self.asked.append(f"pdf {version}")


@pytest.fixture
def held_here(monkeypatch):
    """The database stubbed away: the versions held here, and what an import writes."""
    from types import SimpleNamespace

    from scripts import upstream

    state = SimpleNamespace(versions=[], written=[], checked=0)
    linked = held_script(upstream_source=DEFAULT_SOURCE)
    linked.upstream_id = 134
    state.script = linked
    scripts_held(monkeypatch, linked=linked, by_name=None)
    monkeypatch.setattr(upstream, "_held_versions", lambda script: state.versions)

    def write(source, row, **kwargs):
        state.written.append(row["version"])
        return SimpleNamespace(version=row["version"], pdf=None)

    def check(*args, **kwargs):
        state.checked += 1

    monkeypatch.setattr(upstream, "import_version", write)
    monkeypatch.setattr(upstream, "_record_check", check)
    return state


def test_checking_one_script_costs_one_request_when_nothing_is_new(held_here):
    from scripts.upstream import sync_script

    held_here.versions = ["1.0.0", "1.1.0", "1.2.0"]
    client = ServingClient("1.0.0", "1.1.0", "1.2.0")

    assert sync_script(held_here.script, client=client) == []
    assert client.asked == ["script"]
    # Still recorded as checked, so last_synced says when the source was last asked.
    assert held_here.checked == 1


def test_checking_one_script_fetches_a_newer_latest_version(held_here):
    from scripts.upstream import sync_script

    held_here.versions = ["1.0.0", "1.1.0"]
    client = ServingClient("1.0.0", "1.1.0", "1.2.0")

    sync_script(held_here.script, client=client)

    assert client.asked == ["script", "version 1.2.0", "pdf 1.2.0"]
    assert held_here.written == ["1.2.0"]


def test_checking_one_script_takes_only_the_latest_of_several_new_versions(held_here):
    from scripts.upstream import sync_script

    held_here.versions = ["1.0.0"]
    client = ServingClient("1.0.0", "1.1.0", "1.2.0", "1.3.0")

    sync_script(held_here.script, client=client)

    # Three requests however many versions came out since the last run.
    assert client.asked == ["script", "version 1.3.0", "pdf 1.3.0"]
    assert held_here.written == ["1.3.0"]


def test_checking_one_script_follows_the_version_the_source_flags_as_latest(held_here):
    from scripts.upstream import sync_script

    held_here.versions = ["1.0.0"]
    client = ServingClient("1.0.0", "1.1.0", "2.0.0", flagged="1.1.0")

    sync_script(held_here.script, client=client)

    assert held_here.written == ["1.1.0"]


def test_checking_one_script_leaves_gaps_and_a_full_sync_fills_them(held_here):
    from scripts.upstream import sync_script

    held_here.versions = ["1.0.0", "2.0.0"]

    routine = ServingClient("1.0.0", "1.5.0", "2.0.0", "2.1.0", "2.2.0")
    sync_script(held_here.script, client=routine)
    assert routine.asked == ["script", "version 2.2.0", "pdf 2.2.0"]

    held_here.versions.append("2.2.0")
    full = ServingClient("1.0.0", "1.5.0", "2.0.0", "2.1.0", "2.2.0")
    sync_script(held_here.script, client=full, full=True)
    assert full.asked == ["script", "version 1.5.0", "pdf 1.5.0", "version 2.1.0", "pdf 2.1.0"]


def test_a_first_import_takes_the_whole_history_oldest_first(held_here):
    client = ServingClient("1.10.0", "1.0.0", "1.9.0")

    _, imported, skipped = import_script("134", client=client, user=None)

    assert held_here.written == ["1.0.0", "1.9.0", "1.10.0"]
    assert [version.version for version in imported] == held_here.written
    assert skipped == 0
    assert len(client.asked) == 1 + 2 * 3


def test_importing_again_fetches_only_what_is_missing(held_here):
    held_here.versions = ["1.0.0", "1.1.0"]
    client = ServingClient("1.0.0", "1.1.0", "1.2.0")

    _, _, skipped = import_script("134", client=client, user=None)

    assert client.asked == ["script", "version 1.2.0", "pdf 1.2.0"]
    assert skipped == 2


# --- The commands stop asking a source that refuses ---------------------------------------


def test_a_full_sync_is_one_script_at_a_time():
    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError, match="needs --script"):
        call_command("sync_upstream", "--full")


def test_sync_reads_each_source_once_and_carries_on_past_one_that_refuses(monkeypatch):
    from io import StringIO
    from types import SimpleNamespace

    from django.core.management import CommandError, call_command

    from scripts import upstream

    other = "http://other.example"
    read = []

    def sync_source(source):
        read.append(source)
        if source == DEFAULT_SOURCE:
            raise Blocked("refused with HTTP 403")
        return SimpleNamespace(imported=[], failed=[], pages=1, first_run=False, limited=False)

    monkeypatch.setattr(upstream, "linked_sources", lambda: [DEFAULT_SOURCE, other])
    monkeypatch.setattr(upstream, "sync_source", sync_source)
    err = StringIO()

    with pytest.raises(CommandError, match="1 problem"):
        call_command("sync_upstream", stdout=StringIO(), stderr=err)

    # Once per source, not once per script, and a refusal from one source is not the other's.
    assert read == [DEFAULT_SOURCE, other]
    assert "refused with HTTP 403" in err.getvalue()


def test_a_dry_run_asks_the_source_nothing(monkeypatch):
    from io import StringIO

    from django.core.management import call_command

    from scripts import upstream

    def must_not_run(*args, **kwargs):
        raise AssertionError("a dry run read the source")

    monkeypatch.setattr(upstream, "linked_sources", lambda: [DEFAULT_SOURCE])
    monkeypatch.setattr(upstream, "cursor_for", lambda source: 1234)
    monkeypatch.setattr(upstream, "sync_source", must_not_run)
    out = StringIO()

    call_command("sync_upstream", "--dry-run", stdout=out)

    assert f"would read {DEFAULT_SOURCE}: down to version 1234" in out.getvalue()


def test_import_script_stops_at_the_first_refusal(monkeypatch):
    from io import StringIO

    from django.core.management import CommandError, call_command

    from scripts import upstream

    asked = []

    def refuse(reference, **kwargs):
        asked.append(reference)
        raise Blocked("refused with HTTP 403")

    monkeypatch.setattr(upstream, "import_script", refuse)
    err = StringIO()

    with pytest.raises(CommandError, match="3 of 3"):
        call_command("import_script", "1", "2", "3", stdout=StringIO(), stderr=err)

    assert asked == ["1"]
    assert "not attempted: 2, 3" in err.getvalue()


def test_the_admin_sync_action_reads_each_source_once_whatever_is_selected(monkeypatch):
    from types import SimpleNamespace

    from django.contrib.admin.sites import AdminSite

    from scripts import models, upstream
    from scripts.admin import ScriptAdmin

    read, said = [], []

    def sync_source(source):
        read.append(source)
        return SimpleNamespace(imported=[], failed=[])

    monkeypatch.setattr(upstream, "sync_source", sync_source)
    model_admin = ScriptAdmin(models.Script, AdminSite())
    monkeypatch.setattr(model_admin, "message_user", lambda request, message, *args, **kwargs: said.append(message))

    def selected(name, source, sync_enabled=True):
        return SimpleNamespace(name=name, upstream_source=source, upstream_id=1, sync_enabled=sync_enabled)

    other = "http://other.example"
    model_admin.sync_now(
        None,
        [
            selected("A", DEFAULT_SOURCE),
            selected("B", DEFAULT_SOURCE),
            selected("C", other),
            selected("D", "http://not-followed.example", sync_enabled=False),
        ],
    )

    # Sorted, so in a stable order: "http://other" sorts before "https://www".
    assert read == [other, DEFAULT_SOURCE]
    assert said == [
        f"{other}: every linked script is up to date.",
        f"{DEFAULT_SOURCE}: every linked script is up to date.",
    ]


# --- The scheduled sync: one read of the newest versions, down to the last one seen -------


class JSONResponse(FakeResponse):
    def __init__(self, payload):
        super().__init__(200, b"", "http://example.test/api/scripts/")
        self.payload = payload

    def json(self):
        return self.payload


def test_the_newest_versions_are_asked_for_newest_first_with_homebrew_and_hybrid():
    # Left to its defaults the list leaves out hybrid and homebrew scripts, and a new
    # version of a linked one of those would never be seen.
    session = FakeSession(JSONResponse({"results": [], "next": None}))

    UpstreamClient(session=session).newest_versions(2)

    (url,) = session.requested
    assert url.startswith(f"{DEFAULT_SOURCE}/api/scripts/?")
    for part in ("ordering=-pk", "include_hybrid=true", "include_homebrew=true", "page=2"):
        assert part in url


class FeedClient:
    """A source's newest-versions list, a page at a time, recording every request."""

    def __init__(self, *pages, refuse_page=None):
        self.pages = pages
        self.refuse_page = refuse_page
        self.asked = []

    def newest_versions(self, page):
        self.asked.append(f"page {page}")
        if page == self.refuse_page:
            raise Blocked("refused with HTTP 403")
        rows = [
            {"pk": pk, "script_id": script_id, "name": f"Script {script_id}", "version": number, "content": []}
            for pk, script_id, number in self.pages[page - 1]
        ]
        return {"results": rows, "next": f"?page={page + 1}" if page < len(self.pages) else None}

    def pdf(self, script_id, version):
        self.asked.append(f"pdf {script_id} {version}")


@pytest.fixture
def feed(monkeypatch):
    """sync_source with the database stubbed away: linked scripts, what they hold, the cursor."""
    from types import SimpleNamespace

    from scripts import upstream

    state = SimpleNamespace(linked={}, held={}, cursor=None, written=[], checked=0)

    def link(upstream_id, *held):
        state.linked[upstream_id] = SimpleNamespace(name=f"Script {upstream_id}", upstream_id=upstream_id)
        state.held[upstream_id] = list(held)

    def write(source, row, **kwargs):
        state.written.append((row["script_id"], row["version"]))
        return SimpleNamespace(version=row["version"], script=state.linked[row["script_id"]])

    def advance(source, pk):
        state.cursor = pk

    def check(source):
        state.checked += 1

    state.link = link
    monkeypatch.setattr(upstream, "_linked_by_upstream_id", lambda source: state.linked)
    monkeypatch.setattr(upstream, "_held_versions", lambda script: state.held[script.upstream_id])
    monkeypatch.setattr(upstream, "cursor_for", lambda source: state.cursor)
    monkeypatch.setattr(upstream, "_advance_cursor", advance)
    monkeypatch.setattr(upstream, "_mark_checked", check)
    monkeypatch.setattr(upstream, "import_version", write)
    return state


def test_sync_reads_down_to_the_last_version_seen_and_no_further(feed):
    from scripts.upstream import sync_source

    feed.cursor = 106
    client = FeedClient(
        [(110, 1, "1.0.0"), (109, 2, "1.0.0"), (108, 3, "1.0.0")],
        [(107, 4, "1.0.0"), (106, 5, "1.0.0"), (105, 6, "1.0.0")],
        [(104, 7, "1.0.0")],
    )

    result = sync_source(DEFAULT_SOURCE, client=client)

    assert client.asked == ["page 1", "page 2"]
    assert result.pages == 2
    assert feed.cursor == 110
    assert feed.checked == 1


def test_a_sync_with_nothing_new_costs_one_request(feed):
    from scripts.upstream import sync_source

    feed.cursor = 110
    feed.link(1, "1.0.0")
    client = FeedClient([(110, 1, "1.0.0"), (109, 2, "1.0.0")], [(108, 3, "1.0.0")])

    result = sync_source(DEFAULT_SOURCE, client=client)

    assert client.asked == ["page 1"]
    assert result.imported == []
    assert feed.cursor == 110


def test_only_new_versions_of_linked_scripts_are_imported_and_only_they_cost_a_pdf(feed):
    from scripts.upstream import sync_source

    feed.cursor = 100
    feed.link(2, "1.0.0")  # gains 1.1.0
    feed.link(3, "2.0.0")  # already holds the latest
    feed.link(4, "3.0.0")  # holds something newer than the source's latest
    client = FeedClient([(110, 1, "1.0.0"), (109, 2, "1.1.0"), (108, 3, "2.0.0"), (107, 4, "2.5.0"), (100, 5, "1.0.0")])

    result = sync_source(DEFAULT_SOURCE, client=client)

    # Script 1 is not linked here; its row costs nothing beyond the page it was on.
    assert client.asked == ["page 1", "pdf 2 1.1.0"]
    assert feed.written == [(2, "1.1.0")]
    assert [version.version for version in result.imported] == ["1.1.0"]


def test_the_first_sync_reads_one_page_and_starts_from_there(feed):
    from scripts.upstream import sync_source

    client = FeedClient([(110, 1, "1.0.0"), (108, 2, "1.0.0")], [(107, 3, "1.0.0")])

    result = sync_source(DEFAULT_SOURCE, client=client)

    assert client.asked == ["page 1"]
    assert result.first_run is True
    assert feed.cursor == 110


def test_the_cursor_stays_put_when_the_source_refuses_partway(feed):
    from scripts.upstream import sync_source

    feed.cursor = 100
    client = FeedClient([(110, 1, "1.0.0")], [(105, 2, "1.0.0")], refuse_page=2)

    with pytest.raises(Blocked):
        sync_source(DEFAULT_SOURCE, client=client)

    # The next run starts from the same place rather than skipping what was not read.
    assert feed.cursor == 100
    assert feed.checked == 0


def test_a_sync_stops_at_the_page_limit_and_says_so(feed, monkeypatch):
    from scripts import upstream

    monkeypatch.setattr(upstream, "MAX_PAGES", 2)
    feed.cursor = 1
    client = FeedClient([(30, 1, "1.0.0")], [(20, 2, "1.0.0")], [(10, 3, "1.0.0")])

    result = upstream.sync_source(DEFAULT_SOURCE, client=client)

    assert client.asked == ["page 1", "page 2"]
    assert result.limited is True
    assert feed.cursor == 30


def test_a_version_that_cannot_be_imported_is_reported_and_the_rest_carry_on(feed):
    from scripts.upstream import sync_source

    feed.cursor = 100
    feed.link(2, "1.0.0")
    feed.link(3, "1.0.0")
    client = FeedClient([(110, 2, "v2 beta"), (109, 3, "1.1.0")])

    result = sync_source(DEFAULT_SOURCE, client=client)

    assert feed.written == [(3, "1.1.0")]
    assert len(result.failed) == 1 and "v2 beta" in result.failed[0]
    assert feed.cursor == 110
