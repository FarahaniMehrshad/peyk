"""Two repairs to how Textual reads the Windows console."""

from __future__ import annotations

import sys
from ctypes import byref, sizeof
from typing import Any

_KEY_EVENT = 1
_SPARE = 8  # records kept free for completing a pair
_WAIT = 20  # milliseconds


def fix_input() -> None:
    if sys.platform != "win32":
        return
    from textual.drivers import win32

    if isinstance(win32.KERNEL32, _Kernel32):
        return
    wait = win32.wait_for_handles

    def wait_briefly(handles: list[Any], timeout: int = -1) -> Any:
        # Textual only notices that a lone Esc is over between waits. With its 100 ms,
        # Esc and then a quick j would be taken for Alt+J.
        return wait(handles, min(timeout, _WAIT) if timeout > 0 else timeout)

    win32.wait_for_handles = wait_briefly
    win32.KERNEL32 = _Kernel32(win32.KERNEL32)


class _Kernel32:
    """Never splits an emoji: half of one at the end of a read ends Textual's input thread."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def ReadConsoleInputW(self, handle: Any, records: Any, size: int, count: Any) -> int:
        array, read = records._obj, count._obj
        done = self._real.ReadConsoleInputW(handle, records, size - _SPARE, count)
        while done and read.value < size and _ends_in_half(array, read.value):
            more = type(read)(0)
            rest = byref(array, read.value * sizeof(array[0]))
            if not self._real.ReadConsoleInputW(handle, rest, 1, byref(more)):
                break
            read.value += more.value
        return done


def _ends_in_half(array: Any, length: int) -> bool:
    """The last key pressed is the first half of a surrogate pair."""
    for i in range(length - 1, -1, -1):
        record = array[i]
        if record.EventType == _KEY_EVENT and record.Event.KeyEvent.bKeyDown:
            char = record.Event.KeyEvent.uChar.UnicodeChar
            return "\ud800" <= char <= "\udbff"
    return False
