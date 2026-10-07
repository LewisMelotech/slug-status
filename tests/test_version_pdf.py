"""Giving an existing version a PDF from its script page, without making a new version.

Imports and sync bring no PDFs, since botcscripts.com does not permit them to be
downloaded, so this is how the versions they add get one.
"""

from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from scripts import models, views

PDF = b"%PDF-1.7\n% a script\n"


class StubUser:
    is_authenticated = True

    def __init__(self, pk, is_staff=False, is_superuser=False):
        self.pk = pk
        self.is_staff = is_staff
        self.is_superuser = is_superuser


class StubAnonymous:
    is_authenticated = False
    is_staff = False
    is_superuser = False
    pk = None


def script(owner_id=None):
    """A real Script, unsaved: the rule reads only its owner."""
    return models.Script(name="Sects and Violets", owner_id=owner_id)


# --- Who may ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "owner_id, user, allowed",
    [
        (1, StubUser(1), True),
        (1, StubUser(2), False),
        (1, StubUser(2, is_staff=True), True),
        (1, StubUser(2, is_superuser=True), True),
        (1, StubAnonymous(), False),
        # No owner, as every imported script: staff only, not anyone who can upload.
        (None, StubUser(2), False),
        (None, StubUser(2, is_staff=True), True),
        (None, StubAnonymous(), False),
    ],
)
def test_only_the_owner_and_staff_may_upload_a_pdf(owner_id, user, allowed):
    assert script(owner_id).may_upload_pdfs(user) is allowed


# --- The route ------------------------------------------------------------------------------


def test_the_route_sits_beside_the_version_it_belongs_to():
    from django.urls import resolve, reverse

    path = reverse("upload_version_pdf", kwargs={"pk": 12, "version": "1.0.0"})

    assert path == "/script/12/1.0.0/pdf"
    assert resolve(path).func is views.upload_version_pdf


def test_an_oversized_request_is_refused_before_it_is_read():
    from django.http import HttpResponse

    from scripts import constants
    from scripts.middleware import UploadSizeLimitMiddleware

    middleware = UploadSizeLimitMiddleware(lambda request: HttpResponse("ok"))
    request = RequestFactory().post(
        "/script/12/1.0.0/pdf",
        data=b"x",
        content_type="application/octet-stream",
        CONTENT_LENGTH=str(constants.MAX_UPLOAD_REQUEST_BYTES + 1),
    )

    assert middleware(request).status_code == 413


# --- The view -------------------------------------------------------------------------------


class StubVersion:
    """The version being given a PDF, recording how it was saved."""

    def __init__(self, owner_id=1, pdf=None):
        self.script = script(owner_id)
        self.script.pk = 12
        self.version = "1.0.0"
        self.pdf = pdf
        self.saved = None

    def save(self, update_fields=None):
        self.saved = update_fields


@pytest.fixture
def upload(monkeypatch, settings):
    """POST a file to the view as ``user``, for a version stubbed in place of the database."""
    settings.UPLOAD_DISABLED = False
    state = SimpleNamespace(version=StubVersion(), said=[])
    monkeypatch.setattr(views, "get_object_or_404", lambda *args, **kwargs: state.version)

    def post(user, content=PDF, name="script.pdf"):
        request = RequestFactory().post(
            "/script/12/1.0.0/pdf",
            {"pdf": SimpleUploadedFile(name, content, content_type="application/pdf"), "next": "/script/12/1.0.0"},
        )
        request.user = user
        request._messages = SimpleNamespace(add=lambda level, message, extra_tags="": state.said.append(message))
        return views.upload_version_pdf(request, pk=12, version="1.0.0")

    state.post = post
    return state


def test_the_owner_adds_a_pdf_to_the_version_itself(upload):
    response = upload.post(StubUser(1))

    assert response.status_code == 302
    assert response.url == "/script/12/1.0.0"
    # The PDF goes on this version, and only that field is written: no new version.
    assert upload.version.saved == ["pdf"]
    assert upload.version.pdf.read() == PDF
    assert upload.said == ["PDF added for Sects and Violets v1.0.0."]


def test_staff_can_replace_a_pdf_on_a_script_with_no_owner(upload):
    upload.version = StubVersion(owner_id=None, pdf="12/1.0.0/old.pdf")

    upload.post(StubUser(2, is_staff=True))

    assert upload.version.saved == ["pdf"]
    assert upload.said == ["PDF replaced for Sects and Violets v1.0.0."]


@pytest.mark.parametrize("user", [StubUser(2), StubAnonymous()])
def test_anyone_else_is_refused_and_nothing_is_written(upload, user):
    with pytest.raises(PermissionDenied):
        upload.post(user)

    assert upload.version.saved is None


def test_a_file_that_is_not_a_pdf_is_refused_with_a_message(upload):
    upload.post(StubUser(1), content=b"<html>not a pdf</html>")

    assert upload.version.saved is None
    assert upload.said == ["This file is not a valid PDF."]


def test_the_upload_switch_holds_owners_but_not_staff(upload, settings):
    settings.UPLOAD_DISABLED = True

    with pytest.raises(PermissionDenied):
        upload.post(StubUser(1))
    upload.post(StubUser(2, is_staff=True))

    assert upload.version.saved == ["pdf"]


def test_only_a_post_is_accepted():
    request = RequestFactory().get("/script/12/1.0.0/pdf")

    assert views.upload_version_pdf(request, pk=12, version="1.0.0").status_code == 405


# --- What the script page tells you afterwards ----------------------------------------------


def test_the_script_page_shows_messages_and_keeps_tab_names_to_itself(monkeypatch):
    # Commenting sends "comments-tab" to say which tab to reopen; the PDF upload, the custom
    # id form and signing in send messages meant to be read, which were being swallowed.
    view = views.ScriptView()
    monkeypatch.setattr(view, "get_object", lambda: SimpleNamespace())
    monkeypatch.setattr(view, "get_context_data", lambda **kwargs: {})
    monkeypatch.setattr(view, "render_to_response", lambda context: context)
    request = RequestFactory().get("/script/12/1.0.0")
    added = SimpleNamespace(message="PDF added for Sects and Violets v1.0.0.", level_tag="success")
    request._messages = [SimpleNamespace(message="comments-tab", level_tag="success"), added]
    view.setup(request, pk=12, version="1.0.0")

    context = view.get(request)

    assert context["activetab"] == "comments-tab"
    assert context["notices"] == [added]
