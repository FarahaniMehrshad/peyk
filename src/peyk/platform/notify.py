"""Windows notifications, one per chat. They never get in the way of the app."""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Protocol

log = logging.getLogger(__name__)
NAME = "Peyk"
SENDER = "Peyk.Telegram"
# Windows shows popups only from senders it knows; this is how to become one.
_SENDER_KEY = rf"Software\Classes\AppUserModelId\{SENDER}"
_GROUP = "chats"
_TERMINALS = ("CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass")
_CLOSING = 2.0  # seconds
_ENDINGS = (2, 5, 6)  # the window is closed, logging off, shutting down


class Notifier(Protocol):
    on_click: Callable[[int], None] | None  # may be called from any thread

    def show(self, chat_id: int, title: str, text: str, silent: bool = False) -> None:
        """Replaces the previous one of the same chat."""
        ...

    def forget(self, chat_id: int) -> None: ...

    def clear(self, for_good: bool = False) -> None:
        """Take them all away. `for_good`: leave no trace in Windows."""
        ...

    def close(self) -> None: ...


class Toaster:
    """Talks to Windows from a thread of its own."""

    def __init__(self) -> None:
        self.on_click: Callable[[int], None] | None = None
        self._thread: ThreadPoolExecutor | None = None
        self._toaster: Any = None
        self._shown: dict[int, Any] = {}
        self._broken = False
        self._closed = False

    def start(self) -> None:
        """Take away what an earlier run left, and arrange for our own end."""
        self._later(self._clear_leftovers)
        on_window_closed(self.close)

    def show(self, chat_id: int, title: str, text: str, silent: bool = False) -> None:
        self._later(self._show, chat_id, title, text, silent)

    def forget(self, chat_id: int) -> None:
        if self._thread:
            self._later(self._forget, chat_id)

    def clear(self, for_good: bool = False) -> None:
        if self._thread or for_good:
            self._later(self._clear, for_good)

    def close(self) -> None:
        job = self._later(self._clear, False) if self._thread else None
        self._closed = True
        if job and self._thread:
            try:
                job.result(_CLOSING)
            except Exception as error:
                log.info("notifications not cleared: %s", type(error).__name__)
            self._thread.shutdown(wait=False, cancel_futures=True)

    def _later(self, work: Callable[..., None], *arguments: Any) -> Future[None] | None:
        if self._closed or sys.platform != "win32":
            return None
        if self._thread is None:
            self._thread = ThreadPoolExecutor(1, "notify")
        return self._thread.submit(self._safely, work, *arguments)

    def _safely(self, work: Callable[..., None], *arguments: Any) -> None:
        if self._broken:
            return
        try:
            work(*arguments)
        except Exception as error:
            log.warning("notification failed", exc_info=error)
            self._broken = self._toaster is None  # not even the first one worked

    # -- in the thread

    def _show(self, chat_id: int, title: str, text: str, silent: bool) -> None:
        from windows_toasts import Toast, ToastAudio

        self._ready()
        toast = Toast([title, text], group=_GROUP)
        toast.tag = str(chat_id)
        toast.audio = ToastAudio(silent=True) if silent else None
        toast.on_activated = lambda _: self._clicked(chat_id)
        self._shown[chat_id] = toast
        self._toaster.show_toast(toast)

    def _ready(self) -> None:
        if self._toaster is None:
            from windows_toasts import InteractableWindowsToaster

            _register()
            self._toaster = InteractableWindowsToaster(NAME, SENDER)

    def _clear_leftovers(self) -> None:
        if _registered():
            self._ready()
            self._toaster.clear_toasts()

    def _forget(self, chat_id: int) -> None:
        toast = self._shown.pop(chat_id, None)
        if toast:
            self._toaster.remove_toast(toast)

    def _clear(self, for_good: bool) -> None:
        self._shown.clear()
        if self._toaster:
            self._toaster.clear_toasts()
        if for_good:
            self._toaster = None
            _unregister()

    def _clicked(self, chat_id: int) -> None:
        self._shown.pop(chat_id, None)
        if self.on_click and not self._closed:
            self.on_click(chat_id)


def _register() -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _SENDER_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, NAME)


def _registered() -> bool:
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SENDER_KEY))
    except FileNotFoundError:
        return False
    return True


def _unregister() -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _SENDER_KEY)
    except FileNotFoundError:
        pass


# -- the window we run in


_handlers: list[Any] = []  # Windows calls them, so they must not go away


def on_window_closed(work: Callable[[], None]) -> None:
    """Closing the window ends the process within seconds, without further notice."""
    if sys.platform != "win32":
        return

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
    def handler(event: int) -> int:
        if event in _ENDINGS:
            work()
        return 0  # and now what Windows does anyway

    _handlers.append(handler)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, 1)


def terminal_window() -> int:
    """The window in front, if it is a terminal. Ask when we know it is ours."""
    if sys.platform != "win32":
        return 0
    window = ctypes.windll.user32.GetForegroundWindow()
    name = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(window, name, 64)
    return window if name.value in _TERMINALS else 0


def bring_to_front(window: int) -> None:
    if sys.platform == "win32" and window and ctypes.windll.user32.IsWindow(window):
        if ctypes.windll.user32.IsIconic(window):
            ctypes.windll.user32.ShowWindow(window, 9)  # restore
        ctypes.windll.user32.SetForegroundWindow(window)
