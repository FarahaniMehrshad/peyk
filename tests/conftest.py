import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from peyk import files
from peyk.paths import Paths
from peyk.platform import clipboard, notify, opener


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """No test may touch the real configuration or account."""
    monkeypatch.setenv("PEYK_HOME", str(tmp_path / "home"))
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
                 "all_proxy"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    return Paths.default()


@pytest.fixture(autouse=True)
def copied(monkeypatch):
    """What was copied; the real clipboard is left alone."""
    texts: list[str] = []

    def copy(text):
        texts.append(text)
        return True

    monkeypatch.setattr(clipboard, "copy", copy)
    return texts


@pytest.fixture(autouse=True)
def launched(monkeypatch):
    """What would have been opened; nothing ever is."""
    things: list[str] = []
    monkeypatch.setattr(opener, "os", SimpleNamespace(startfile=things.append))
    monkeypatch.setattr(opener, "subprocess", SimpleNamespace(Popen=things.append))
    return things


@pytest.fixture(autouse=True)
def windows(monkeypatch):
    """What Windows would have shown; it is never asked to."""
    state = SimpleNamespace(
        senders=[], shown=[], removed=[], cleared=0, registered=False, trouble=None,
        threads=set(),
    )  # fmt: skip

    class Toaster:
        def __init__(self, name, sender):
            state.senders.append((name, sender))

        def show_toast(self, toast):
            state.threads.add(threading.current_thread().name)
            self._try()
            state.shown.append(toast)

        def remove_toast(self, toast):
            self._try()
            state.removed.append(toast)

        def clear_toasts(self):
            state.cleared += 1

        def _try(self):
            if state.trouble:
                raise state.trouble

    class Toast:
        def __init__(self, text_fields, group):
            self.text_fields, self.group = text_fields, group
            self.tag, self.audio, self.on_activated = "", None, None

    def register(value):
        if value and state.trouble:
            raise state.trouble
        state.registered = value

    fake = ModuleType("windows_toasts")
    fake.InteractableWindowsToaster, fake.Toast = Toaster, Toast
    fake.ToastAudio = lambda silent: SimpleNamespace(silent=silent)
    monkeypatch.setitem(sys.modules, "windows_toasts", fake)
    monkeypatch.setattr(notify, "_register", lambda: register(True))
    monkeypatch.setattr(notify, "_unregister", lambda: register(False))
    monkeypatch.setattr(notify, "_registered", lambda: state.registered)
    monkeypatch.setattr(notify, "terminal_window", lambda: 1)
    return state


@pytest.fixture
def fronted(monkeypatch):
    windows = []
    monkeypatch.setattr(notify, "bring_to_front", windows.append)
    return windows


@pytest.fixture(autouse=True)
def downloads(tmp_path, monkeypatch):
    from peyk.ui.screens import main

    folder = tmp_path / "Downloads" / "Peyk"
    folder.parent.mkdir()
    for module in (files, main):
        monkeypatch.setattr(module, "default_folder", lambda: folder)
    return folder


@pytest.fixture(autouse=True)
def quick_reading(monkeypatch):
    from peyk.ui.screens import main

    monkeypatch.setattr(main, "READ_DELAY", 0.05)
