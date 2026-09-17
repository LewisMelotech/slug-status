import inspect
import re

import pytest
from django.template.loader import render_to_string

from scripts import minecraft, slugs

# A Minecraft function id is namespace:path, each limited to these characters.
MINECRAFT_NAMESPACE = re.compile(r"^[a-z0-9_.-]+$")
MINECRAFT_PATH = re.compile(r"^[a-z0-9_./-]+$")


def test_both_commands_are_built_from_the_custom_id():
    assert minecraft.commands_for("sects") == (
        "/function botc_nw_lite:roles/sects",
        "/function botc_nw_lite:scripts/sects",
    )


@pytest.mark.parametrize("custom_id", [None, ""])
def test_no_custom_id_means_no_commands(custom_id):
    assert minecraft.commands_for(custom_id) == ()


@pytest.mark.parametrize("custom_id", ["x", "tb", "sects-and-violets", "no-greater-joy-2", "123abc"])
def test_every_valid_custom_id_makes_a_valid_minecraft_function_id(custom_id):
    """A custom id the site accepts must never produce a command the game rejects."""
    assert slugs.SLUG_PATTERN.match(custom_id), "the example is not itself a valid custom id"
    for command in minecraft.commands_for(custom_id):
        function_id = command.removeprefix("/function ")
        namespace, _, path = function_id.partition(":")
        assert MINECRAFT_NAMESPACE.match(namespace), command
        assert MINECRAFT_PATH.match(path), command


def test_the_custom_id_rule_admits_no_character_minecraft_would_refuse():
    """Asked of the rule itself rather than a list of examples, so it keeps holding if the
    custom id rule is ever loosened — at which point it fails, rather than the game."""
    import string

    admitted = {
        character
        for character in string.printable
        if slugs.SLUG_PATTERN.match(character) or slugs.SLUG_PATTERN.match(f"a{character}a")
    }
    assert admitted <= set(string.ascii_lowercase + string.digits + "_.-/")


# --- The Minecraft CMD menu ---------------------------------------------------------------


def test_the_menu_offers_both_commands_to_copy_in_order():
    html = render_to_string("minecraft_commands.html", {"minecraft_commands": minecraft.commands_for("sects")})
    assert ">Minecraft CMD</button>" in html
    roles = html.index('data-minecraft-command="/function botc_nw_lite:roles/sects"')
    scripts = html.index('data-minecraft-command="/function botc_nw_lite:scripts/sects"')
    assert roles < scripts
    assert html.count('onclick="copyMinecraftCommand(this)"') == 2


def test_a_script_without_a_custom_id_gets_no_menu():
    html = render_to_string("minecraft_commands.html", {"minecraft_commands": ()})
    assert html.strip() == ""


def test_the_script_page_shows_the_menu():
    from django.template.loader import get_template

    assert '{% include "minecraft_commands.html" %}' in get_template("script.html").template.source


def test_the_script_page_is_given_the_commands():
    from scripts import views

    source = inspect.getsource(views.ScriptView.get_context_data)
    assert 'context["minecraft_commands"] = minecraft.commands_for(' in source


def _menu_script():
    html = render_to_string("minecraft_commands.html", {"minecraft_commands": minecraft.commands_for("sects")})
    return html[html.index("<script>") : html.index("</script>")]


def test_a_refused_clipboard_still_falls_back_to_a_selection_copy():
    """Regression, found by clicking the menu in a real browser.

    The Clipboard API can exist and still refuse — an embedded browser did exactly that
    — and the fallback only ran when the API was missing, so the copy just failed.
    """
    script = _menu_script()
    api = script.index("navigator.clipboard.writeText(text)")
    fallback = script.index("copyBySelection(text)", api)
    assert ".catch(" in script[api:fallback], "the selection copy no longer runs when the API refuses"


def test_the_menu_script_is_plain_ascii():
    """Regression: an em dash in a message rendered as mojibake.

    The site sends no charset in the page itself and relies on the HTTP header, so any
    non-ASCII in inline script text is one misconfigured proxy away from garbage.
    """
    assert _menu_script().isascii()
