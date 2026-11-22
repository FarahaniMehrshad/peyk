"""Put text on the Windows clipboard."""

from __future__ import annotations

import sys
import time

_UNICODE_TEXT = 13
_MOVEABLE = 2


def copy(text: str) -> bool:
    """False if another program holds the clipboard, or this is not Windows."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-16-le") + b"\0\0"
    for _ in range(5):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return False
    try:
        handle = kernel32.GlobalAlloc(_MOVEABLE, len(data))
        if not handle:
            return False
        ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
        kernel32.GlobalUnlock(handle)
        user32.EmptyClipboard()
        if not user32.SetClipboardData(_UNICODE_TEXT, handle):
            kernel32.GlobalFree(handle)  # on success Windows owns it
            return False
        return True
    finally:
        user32.CloseClipboard()
