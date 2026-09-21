import pytest
from django.core.exceptions import ValidationError

from scripts.slugs import RESERVED_SLUGS, SLUG_PATTERN, normalise_slug, validate_script_slug


@pytest.mark.parametrize(
    "value, expected",
    [
        ("sects", "sects"),
        ("SECTS", "sects"),
        ("  Sects-And-Violets  ", "sects-and-violets"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_normalise_slug(value, expected):
    assert normalise_slug(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "sects",
        "sects-and-violets",
        "2nd-edition",
        "tb2",
        "SECTS",
        "  Sects  ",
        "a",
        "a" * 50,
    ],
)
def test_valid_slugs_are_accepted(value):
    validate_script_slug(value)


@pytest.mark.parametrize(
    "value, code",
    [
        # A slug shares its URL position with the numeric script id, and clients
        # route on "is this all digits?", so an integer-looking slug would
        # silently resolve to a different script.
        ("13108", "numeric"),
        ("007", "numeric"),
        ("-12", "numeric"),
        ("+12", "numeric"),
        ("1_0", "numeric"),
        # Reserved because /script/<slug> would shadow an existing route.
        ("search", "reserved"),
        ("upload", "reserved"),
        ("api", "reserved"),
        ("admin", "reserved"),
        ("all-roles", "reserved"),
        # Not URL-safe, or not the one canonical spelling.
        ("has spaces", "invalid"),
        ("has_underscore", "invalid"),
        ("-leading-hyphen", "invalid"),
        ("trailing-hyphen-", "invalid"),
        ("double--hyphen", "invalid"),
        ("café", "invalid"),
        ("sects/violets", "invalid"),
        # Length.
        ("a" * 51, "too_long"),
        ("", "blank"),
        ("   ", "blank"),
    ],
)
def test_invalid_slugs_are_rejected(value, code):
    with pytest.raises(ValidationError) as excinfo:
        validate_script_slug(value)
    assert excinfo.value.code == code


def test_reserved_slugs_are_all_reachable():
    """
    Every reserved word must be spellable as a slug, or reserving it is dead
    code that hides a route we think we are protecting.
    """
    unreachable = [slug for slug in RESERVED_SLUGS if not SLUG_PATTERN.match(slug)]
    assert unreachable == []


def _route_segments():
    """(first, second) path segment of every route the site serves, from the real URL table."""
    import re

    from django.urls import get_resolver
    from django.urls.resolvers import URLResolver

    def walk(patterns, prefix):
        for entry in patterns:
            text = prefix + str(entry.pattern).lstrip("^")
            if isinstance(entry, URLResolver):
                yield from walk(entry.url_patterns, text)
            else:
                parts = re.split(r"/", text.replace("$", ""), maxsplit=2)
                yield (parts + ["", ""])[:2]

    return set(map(tuple, walk(get_resolver().url_patterns, "")))


def test_every_top_level_route_segment_is_reserved():
    """A slug that read as a site route would look like a link to somewhere else entirely.

    Taken from the URL table itself, so a route added later fails here instead of
    quietly being left off the list, which is how "server" and "password" were.
    """
    reachable = {
        first
        for first, _ in _route_segments()
        # Only segments a slug could be: "robots.txt" and "all_roles" are unreachable anyway.
        if SLUG_PATTERN.match(first)
    }

    assert reachable - RESERVED_SLUGS == set()


def test_every_literal_child_of_script_is_reserved():
    """/script/<slug> is registered last, so a literal route under script/ would be shadowed."""
    literal_children = {
        second for first, second in _route_segments() if first == "script" and SLUG_PATTERN.match(second)
    }

    assert literal_children - RESERVED_SLUGS == set()


def test_a_blank_slug_filter_is_the_unfiltered_list_and_a_real_one_narrows_it():
    """Pins what filter_slug's guard comment says, since the guard itself is unreachable.

    A blank ?slug= never reaches the method, because django-filter skips empty values and
    the form strips whitespace first. So it is the plain list, like any other empty filter.
    """
    from scripts import filters, models

    def narrowed(raw):
        result = filters.ScriptFilter(data={"slug": raw}, queryset=models.Script.objects.all())
        return bool(result.qs.query.where.children)

    assert narrowed("") is False
    assert narrowed("   ") is False
    assert narrowed("sects") is True
