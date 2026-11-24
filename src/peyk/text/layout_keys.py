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
    user32 = _user32()
    return _lookup(char, _active_layout(user32), _installed_layouts(user32))


@lru_cache(maxsize=1)
def _user32():  # noqa: ANN202
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(wintypes.HKL)]
    user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    user32.GetKeyboardLayout.restype = wintypes.HKL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.VkKeyScanExW.argtypes = [wintypes.WCHAR, wintypes.HKL]
    user32.VkKeyScanExW.restype = ctypes.c_short
    user32.MapVirtualKeyExW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.HKL]
    user32.MapVirtualKeyExW.restype = wintypes.UINT
    return user32


def _active_layout(user32) -> int:  # noqa: ANN001
    """The layout of the window being typed in: the terminal, not this process.

    Several installed layouts can type the same character on different keys
    (Persian and Persian Standard do), so the one in use must be asked first.
    """
    window = user32.GetForegroundWindow()
    if not window:
        return 0
    thread = user32.GetWindowThreadProcessId(window, None)
    if not thread:
        return 0
    return user32.GetKeyboardLayout(thread) or 0


def _installed_layouts(user32) -> tuple[int, ...]:  # noqa: ANN001
    from ctypes import wintypes

    count = user32.GetKeyboardLayoutList(0, None)
    layouts = (wintypes.HKL * count)()
    count = user32.GetKeyboardLayoutList(count, layouts)
    return tuple(layout or 0 for layout in layouts[:count])


@lru_cache(maxsize=1024)
def _lookup(char: str, active: int, installed: tuple[int, ...]) -> str:
    user32 = _user32()
    order = ([active] if active else []) + [layout for layout in installed if layout != active]
    for layout in order:
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
