import pytest

from peyk.text.sanitize import sanitize, sanitize_line


@pytest.mark.parametrize(
    "payload", ["\x1b[31mred", "\x1b]0;title\x07", "\x9b31m", "\x1bP0q\x1b\\", "a\x00b\x7f"]
)
def test_escape_sequences_lose_their_introducers(payload):
    assert all(ord(c) >= 32 and not 0x7F <= ord(c) <= 0x9F for c in sanitize(payload))


def test_useful_characters_survive():
    assert sanitize("می\u200cخواهم 👨\u200d👩\u200d👧") == "می\u200cخواهم 👨\u200d👩\u200d👧"


def test_newlines_tabs_and_surrogates():
    assert sanitize("a\r\nb\rc\td\u2028e") == "a\nb\nc    d\ne"
    assert sanitize("x\ud800y") == "xy"


def test_single_line():
    assert sanitize_line(" a\nb\x1b ") == "a b"
