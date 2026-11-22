"""Map a typed character back to the physical key, as labelled on a US keyboard.

With a Persian layout active the J key types a Persian letter, so vim keys would
stop working. Windows can tell us which key produced a character.
"""

from __future__ import annotations

import sys
from functools import lru_cache

_ROWS = {
    0x02: ("1234567890-=", "!@#$%^&*()_+"),
    0x10: ("qwertyuiop[]", "QWERTYUIOP{}"),
    0x1E: ("asdfghjkl;'`", 'ASDFGHJKL:"~'),
    0x2B: ("\\zxcvbnm,./", "|ZXCVBNM<>?"),
}
SCAN_TO_US: dict[int, tuple[str, str]] = {
    first + i: (plain, shifted)
    for first, (lower, upper) in _ROWS.items()
    for i, (plain, shifted) in enumerate(zip(lower, upper, strict=True))
}


def to_us_key(char: str) -> str:
    """US-keyboard character for the key that types `char`; `char` if unknown."""
    if len(char) != 1 or char.isascii() or ord(char) > 0xFFFF or sys.platform != "win32":
        return char
    return _lookup(char)


@lru_cache(maxsize=512)
def _lookup(char: str) -> str:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(wintypes.HKL)]
    user32.VkKeyScanExW.argtypes = [wintypes.WCHAR, wintypes.HKL]
    user32.VkKeyScanExW.restype = ctypes.c_short
    user32.MapVirtualKeyExW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.HKL]
    user32.MapVirtualKeyExW.restype = wintypes.UINT

    count = user32.GetKeyboardLayoutList(0, None)
    layouts = (wintypes.HKL * count)()
    user32.GetKeyboardLayoutList(count, layouts)
    for layout in layouts:
        scan = user32.VkKeyScanExW(char, layout)
        if scan == -1:
            continue
        virtual_key, modifiers = scan & 0xFF, (scan >> 8) & 0xFF
        if modifiers & 0b110:  # needs Ctrl or Alt: not a plain key press
            continue
        key = SCAN_TO_US.get(user32.MapVirtualKeyExW(virtual_key, 0, layout))
        if key:
            return key[modifiers & 1]
    return char
