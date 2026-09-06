"""
Normalisation and validation for custom script slugs.

A slug is an optional, human-chosen alternative identifier for a Script. It is
canonical in the same sense the numeric primary key is: ``/script/<slug>`` and
``/api/script_ids/slug/<slug>/`` resolve to exactly the same Script, and API
clients are expected to store it as a stable id.

Because a slug shares its URL position with the numeric pk, and because clients
route on "is this query all digits?", a slug that parses as an integer would
silently resolve to a *different* script. That is the single most important rule
enforced here, and it is enforced on the model field so that every write path
(admin, API, ORM ``full_clean``) gets it.
"""

import re

from django.core.exceptions import ValidationError

from scripts import constants

# Lowercase ASCII letters and digits, separated by single hyphens. Stricter than
# Django's own SlugField validator, which also permits underscores and uppercase:
# a slug has to survive being retyped from memory, so there is exactly one
# spelling of any given slug.
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Path segments a slug must never take, because /script/<slug> would otherwise
# shadow an existing route. The first group are the literal children of
# "script/" in scripts/urls.py, which is the shadowing that can actually happen;
# the second are the action segments that follow a script in a URL, reserved so
# that /script/<slug>/<action> stays unambiguous if such routes are ever added;
# the third are the site's top-level segments, reserved so that a slug can never
# read as a link to somewhere else entirely.
#
# Route segments containing an underscore or a dot ("all_roles", "download_pdf",
# "robots.txt") are already unreachable under SLUG_PATTERN and are deliberately
# not repeated here.
RESERVED_SLUGS = frozenset(
    {
        # script/... routes
        "all-roles",
        "search",
        "upload",
        # action segments that follow a script
        "delete",
        "download",
        "edit",
        "favourite",
        "favourites",
        "json",
        "new",
        "results",
        "similar",
        "slug",
        "vote",
        # top-level site segments
        "account",
        "admin",
        "api",
        "collection",
        "collections",
        "comment",
        "comments",
        "health-check",
        "login",
        "logout",
        "media",
        "robots",
        "script",
        "script-ids",
        "scripts",
        "signup",
        "static",
        "statistics",
        "update",
        "worldcup",
    }
)


def normalise_slug(value: str | None) -> str | None:
    """
    Reduce user input to the one canonical spelling of a slug.

    Returns None for anything empty, so that "clear the slug" and "no slug" are
    the same stored value. NULL rather than "" matters: the uniqueness
    constraint treats NULLs as distinct, so any number of scripts can be
    unslugged, but every "" would collide.
    """
    if value is None:
        return None
    slug = value.strip().lower()
    return slug or None


def _parses_as_integer(value: str) -> bool:
    """
    Whether int() would accept this string. Deliberately broader than
    str.isdigit(): int() also accepts surrounding whitespace, a leading sign and
    embedded underscores ("1_0" is 10), and any of those spellings would be a
    slug that some client resolves as a numeric script id.
    """
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def validate_script_slug(value: str) -> None:
    """
    Validate a slug, case-insensitively. Storage is canonicalised to lowercase
    by Script.save(), so a mixed-case slug is accepted here and normalised
    rather than rejected.
    """
    if value is None:
        return

    slug = value.strip().lower()

    if not slug:
        raise ValidationError(
            "Enter a slug, or leave this blank to remove the script's existing slug.",
            code="blank",
        )
    if len(slug) < constants.MIN_SLUG_LENGTH:
        raise ValidationError(
            "A slug must be at least %(min)d characters long.",
            code="too_short",
            params={"min": constants.MIN_SLUG_LENGTH},
        )
    if len(slug) > constants.MAX_SLUG_LENGTH:
        raise ValidationError(
            "A slug must be at most %(max)d characters long.",
            code="too_long",
            params={"max": constants.MAX_SLUG_LENGTH},
        )
    if _parses_as_integer(slug):
        raise ValidationError(
            "'%(slug)s' is a number, and numbers are reserved for script ids. "
            "Add a letter so the slug cannot be mistaken for a script id.",
            code="numeric",
            params={"slug": slug},
        )
    if not SLUG_PATTERN.match(slug):
        raise ValidationError(
            "'%(slug)s' is not a valid slug. Use lowercase letters and digits "
            "separated by single hyphens, for example 'sects-and-violets'.",
            code="invalid",
            params={"slug": slug},
        )
    if slug in RESERVED_SLUGS:
        raise ValidationError(
            "'%(slug)s' is reserved because it is part of a site URL. Choose another slug.",
            code="reserved",
            params={"slug": slug},
        )
