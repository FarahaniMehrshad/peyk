import sys

import pytest

from peyk.text.layout_keys import SCAN_TO_US, _installed_layouts, _lookup, _user32, to_us_key


def test_scan_table():
    assert SCAN_TO_US[0x24] == ("j", "J")
    assert SCAN_TO_US[0x25] == ("k", "K")
    assert SCAN_TO_US[0x35] == ("/", "?")
    assert SCAN_TO_US[0x29] == ("`", "~")


def test_ascii_emoji_and_unknown_characters_pass_through():
    for char in ("j", "?", ":", "👍", "ctrl+a", "", "中"):
        assert to_us_key(char) == char


@pytest.mark.skipif(sys.platform != "win32", reason="asks Windows about its keyboard layouts")
def test_every_installed_layout_maps_back_to_its_own_keys():
    """Layouts can share characters on different keys: the active one decides."""
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    user32.ToUnicodeEx.argtypes = [
        wintypes.UINT, wintypes.UINT, ctypes.c_char_p, wintypes.LPWSTR,
        ctypes.c_int, wintypes.UINT, wintypes.HKL,
    ]
    installed = _installed_layouts(user32)
    wrong = []
    for layout in installed:
        keys_for: dict[str, list[str]] = {}
        for scan, keys in SCAN_TO_US.items():
            virtual_key = user32.MapVirtualKeyExW(scan, 1, layout)
            for shift, key in enumerate(keys):
                state = bytearray(256)
                state[0x10] = 0x80 if shift else 0
                out = ctypes.create_unicode_buffer(8)
                # Flag 4: leave the layout's dead-key state alone.
                typed = user32.ToUnicodeEx(virtual_key, scan, bytes(state), out, 8, 4, layout)
                if typed == 1 and len(out.value) == 1 and not out.value.isascii():
                    keys_for.setdefault(out.value, []).append(key)
        for char, keys in keys_for.items():
            if len(keys) > 1:  # typed by two keys: nothing tells them apart
                continue
            got = _lookup(char, layout, installed)
            if got != keys[0]:
                wrong.append((hex(layout), char, keys[0], got))
    assert not wrong
