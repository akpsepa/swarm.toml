from src.strings import shout, first_char


def test_shout():
    assert shout("hi") == "HI!"


def test_first_char():
    assert first_char("abc") == "a"
