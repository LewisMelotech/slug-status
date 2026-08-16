import pytest

from scripts.script_json import name_to_id


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Snake Charmer", "snakecharmer"),
        ("Mutant", "mutant"),
        ("Fortune Teller", "fortuneteller"),
        ("Pit-Hag", "pit-hag"),
        ("Lil' Monsta", "lilmonsta"),
        ("snakecharmer", "snakecharmer"),
        ("SNAKE CHARMER", "snakecharmer"),
        ("  Snake  Charmer  ", "snakecharmer"),
        ("", ""),
    ],
)
def test_name_to_id(name, expected):
    assert name_to_id(name) == expected
