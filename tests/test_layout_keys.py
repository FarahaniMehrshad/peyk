from peyk.text.layout_keys import SCAN_TO_US, to_us_key


def test_scan_table():
    assert SCAN_TO_US[0x24] == ("j", "J")
    assert SCAN_TO_US[0x25] == ("k", "K")
    assert SCAN_TO_US[0x35] == ("/", "?")
    assert SCAN_TO_US[0x29] == ("`", "~")


def test_ascii_emoji_and_unknown_characters_pass_through():
    for char in ("j", "?", ":", "👍", "ctrl+a", "", "中"):
        assert to_us_key(char) == char
