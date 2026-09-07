import pytest

from scripts.upstream import (
    DEFAULT_SOURCE,
    UpstreamClient,
    UpstreamError,
    _latest_of,
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

    form = ScriptImportForm(data={"reference": "http://botc-scripts:8000/script/7"})
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
