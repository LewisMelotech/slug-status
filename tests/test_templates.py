"""Template mistakes that render without an error and are only noticed on the page."""

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
