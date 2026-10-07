"""Giving an existing version a PDF from its script page, without making a new version.

Imports and sync bring no PDFs, since botcscripts.com does not permit them to be
downloaded, so this is how the versions they add get one.
"""

from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from scripts import constants, forms, models, upstream, views

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
    assert script(owner_id).may_manage(user) is allowed


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


# --- Giving a PDF while importing -------------------------------------------------------------


class ImportedScript(SimpleNamespace):
    """What import_script hands back: the real rule, and the newest version a PDF goes on."""

    def may_manage(self, user):
        return models.Script.may_manage(self, user)

    def latest_version(self):
        return self.newest


def imported_script(owner_id=None, imported_by_id=None, newest_pdf=None):
    newest = StubVersion(owner_id=owner_id, pdf=newest_pdf)
    newest.version = "1.1.0"
    return ImportedScript(
        pk=7,
        name="Sects and Violets",
        owner_id=owner_id,
        imported_by_id=imported_by_id,
        sync_enabled=False,
        minecraft_customisations="",
        versions=SimpleNamespace(count=lambda: 2),
        newest=newest,
        saved=None,
    )


def _import_with_pdf(monkeypatch, user, script, imported, content=PDF):
    """Submit the import page as ``user``, with the import itself stubbed, and say what it said."""
    monkeypatch.setattr(upstream, "import_script", lambda *args, **kwargs: (script, imported, 0))
    view = views.ScriptImportView()
    view.request = RequestFactory().post("/script/import")
    view.request.user = user
    said = []
    view.request._messages = SimpleNamespace(add=lambda level, message, extra_tags="": said.append(message))
    pdf = SimpleUploadedFile("script.pdf", content, content_type="application/pdf") if content is not None else None
    form = SimpleNamespace(cleaned_data={"upstream_id": 134, "link": True, "minecraft_customisations": "", "pdf": pdf})
    view.form_valid(form)
    return said


def test_a_signed_in_importer_of_a_new_script_gives_its_newest_version_the_pdf(monkeypatch):
    # They are recorded as having imported it, which is what lets them look after it.
    script = imported_script(imported_by_id=5)

    said = _import_with_pdf(monkeypatch, StubUser(5), script, [SimpleNamespace(version="1.1.0")])

    assert script.newest.saved == ["pdf"]
    assert script.newest.pdf.read() == PDF
    assert "PDF added for Sects and Violets v1.1.0." in said


def test_a_pdf_on_import_replaces_the_one_the_version_has(monkeypatch):
    script = imported_script(imported_by_id=5, newest_pdf="7/1.1.0/old.pdf")

    said = _import_with_pdf(monkeypatch, StubUser(5), script, [SimpleNamespace(version="1.1.0")])

    assert script.newest.saved == ["pdf"]
    assert "PDF replaced for Sects and Violets v1.1.0." in said


def test_only_the_newest_version_gets_it_however_many_the_import_brought(monkeypatch):
    script = imported_script(imported_by_id=5)
    older = StubVersion(owner_id=None)

    _import_with_pdf(monkeypatch, StubUser(5), script, [older, SimpleNamespace(version="1.1.0")])

    assert script.newest.saved == ["pdf"]
    assert older.saved is None


def test_importing_a_script_already_held_still_gives_its_newest_version_the_pdf(monkeypatch):
    # Nothing new came in, so this is the same as using the PDF button on the script page.
    script = imported_script(owner_id=5)

    said = _import_with_pdf(monkeypatch, StubUser(5), script, [])

    assert script.newest.saved == ["pdf"]
    assert "PDF added for Sects and Violets v1.1.0." in said
    assert not any(message.startswith("Imported") for message in said)


def test_staff_may_give_any_script_a_pdf_on_import(monkeypatch):
    script = imported_script(owner_id=1)

    _import_with_pdf(monkeypatch, StubUser(9, is_staff=True), script, [])

    assert script.newest.saved == ["pdf"]


@pytest.mark.parametrize("user", [StubUser(5), StubAnonymous()])
def test_anyone_else_imports_but_the_pdf_is_left_off_and_they_are_told(monkeypatch, user):
    # Someone else's script, or no account to look after one: the same people the Upload PDF
    # button refuses. The import itself is theirs to do, so it still goes ahead.
    script = imported_script(owner_id=1)

    said = _import_with_pdf(monkeypatch, user, script, [SimpleNamespace(version="1.1.0")])

    assert script.newest.saved is None
    assert any(message.startswith("Imported Sects and Violets") for message in said)
    assert any("PDF was not added" in message for message in said)


def test_no_pdf_leaves_everything_and_the_note_about_pdfs_alone(monkeypatch):
    script = imported_script(imported_by_id=5)

    said = _import_with_pdf(monkeypatch, StubUser(5), script, [SimpleNamespace(version="1.1.0")], content=None)

    assert script.newest.saved is None
    assert any("without PDFs" in message for message in said)


def test_the_note_about_missing_pdfs_is_not_shown_when_one_was_just_added(monkeypatch):
    script = imported_script(imported_by_id=5)

    said = _import_with_pdf(monkeypatch, StubUser(5), script, [SimpleNamespace(version="1.1.0")])

    assert not any("without PDFs" in message for message in said)


@pytest.mark.parametrize("user, offered", [(StubAnonymous(), False), (StubUser(5), True)])
def test_the_pdf_box_is_only_offered_to_someone_signed_in(user, offered):
    view = views.ScriptImportView()
    request = RequestFactory().get("/script/import")
    request.user = user
    view.setup(request)

    assert ("pdf" in view.get_form().fields) is offered


def _import_form(content=PDF, name="script.pdf"):
    files = {"pdf": SimpleUploadedFile(name, content, content_type="application/pdf")} if content is not None else {}
    return forms.ScriptImportForm({"reference": "134"}, files)


def test_the_import_form_does_not_need_a_pdf():
    form = _import_form(content=None)

    assert form.is_valid(), form.errors
    assert not form.cleaned_data.get("pdf")


def test_the_import_form_takes_a_real_pdf():
    form = _import_form()

    assert form.is_valid(), form.errors
    assert form.cleaned_data["pdf"].read() == PDF


@pytest.mark.parametrize(
    "content, name, message",
    [
        (b"<html>not a pdf</html>", "script.pdf", "not a valid PDF"),
        (PDF, "script.txt", "extension"),
    ],
)
def test_the_import_form_checks_the_pdf_as_the_upload_form_does(content, name, message):
    form = _import_form(content, name)

    assert not form.is_valid()
    assert message in " ".join(form.errors["pdf"])


def test_the_import_form_refuses_a_pdf_over_the_size_limit(monkeypatch):
    monkeypatch.setattr(constants, "MAX_PDF_UPLOAD_BYTES", 10)

    form = _import_form()

    assert not form.is_valid()
    assert "pdf" in form.errors


def test_every_form_that_takes_a_pdf_checks_it_the_same_way():
    """One definition, so a check added to one cannot be missing from the others."""
    checks = [
        form.base_fields["pdf"].validators for form in (forms.ScriptForm, forms.VersionPdfForm, forms.ScriptImportForm)
    ]

    assert checks[0] == checks[1] == checks[2]


def test_the_import_page_can_carry_a_file_and_has_the_pdf_box():
    from django.template.loader import render_to_string

    html = render_to_string("import.html", {"form": forms.ScriptImportForm(), "user": StubUser(5)})

    assert 'enctype="multipart/form-data"' in html
    assert 'name="pdf"' in html


def test_the_pdf_section_sits_on_its_own_row_below_the_buttons():
    """Not a column in the button row, where it pushed the other buttons onto a second line.

    Read from the template source, in the order a reader of the page meets things: the
    button row ends at its alert-messages cell, and the PDF form comes after that and
    before the Minecraft customisations form that already had a row to itself.
    """
    from pathlib import Path

    source = (Path(views.__file__).parent / "templates" / "script.html").read_text(encoding="utf-8")

    row_end = source.index('class="p-1 alert-messages text-center"')
    pdf = source.index("upload_version_pdf")
    minecraft = source.index("set_minecraft_customisations")

    assert source.count("upload_version_pdf") == 1
    assert row_end < pdf < minecraft


def test_the_import_page_puts_the_pdf_box_last():
    from django.template.loader import render_to_string

    html = render_to_string("import.html", {"form": forms.ScriptImportForm(), "user": StubUser(5)})

    assert html.index('name="minecraft_customisations"') < html.index('name="pdf"')
    assert html.count('name="pdf"') == 1
