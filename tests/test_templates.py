"""Template mistakes that render without an error and are only noticed on the page."""

import re
from pathlib import Path

from django.template.base import Lexer, TokenType

TEMPLATES = Path(__file__).resolve().parent.parent / "scripts" / "templates"


def test_no_template_has_a_comment_that_runs_over_a_line():
    """Django's {# ... #} comment must open and close on one line.

    Given a comment that spans several lines, Django does not complain: it prints the
    whole thing, braces and all, into the page. {% comment %} is the multi-line form.
    The lexer is asked, rather than a regex, so this fails exactly when Django would fail.
    """
    offenders = [
        # A text token's lineno is where the token starts, so add the lines before the {#.
        f"{path.relative_to(TEMPLATES.parent.parent)}"
        f":{token.lineno + token.contents[: token.contents.index('{#')].count(chr(10))}"
        for path in sorted(TEMPLATES.rglob("*.html"))
        for token in Lexer(path.read_text(encoding="utf-8")).tokenize()
        if token.token_type == TokenType.TEXT and "{#" in token.contents
    ]

    assert not offenders, "multi-line {# #} comments render as visible text; use {% comment %}: " + ", ".join(offenders)


def test_no_template_builds_a_link_to_the_original_site_from_this_instances_own_ids():
    """The script page's JSON button copied https://www.botcscripts.com/api/scripts/<pk>/json/.

    That is the original site's address, and <pk> is this instance's version number, so on a
    self-hosted copy it named a different script or none. Links to the original site that
    carry no id of ours, such as the import page's, are meant, and are not what this checks.
    """
    offenders = [
        path.name
        for path in sorted(TEMPLATES.rglob("*.html"))
        if re.search(r"botcscripts\.com/[^\"'\s]*\{\{", path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_the_copied_json_link_uses_this_instances_address_and_its_own_route():
    source = (TEMPLATES / "script.html").read_text(encoding="utf-8")
    body = source[source.index("function CopyLink()") :][:600]

    # The configured address, else the one the page came from: never a fixed host.
    assert "SITE_URL" in body
    assert "window.location.origin" in body
    # The route by name, so the path cannot drift from the one the API serves.
    assert "{% url 'scriptversion-json' script_version.pk %}" in body


def test_the_configured_site_address_reaches_every_page(settings):
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    from scripts import context_processors

    settings.SITE_URL = "https://scripts.example.com"
    settings.UPLOAD_DISABLED = False
    settings.BANNER = None
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    assert context_processors.custom_configuration(request)["SITE_URL"] == "https://scripts.example.com"
