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
