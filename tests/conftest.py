from types import SimpleNamespace

import pytest

from peyk import files
from peyk.paths import Paths
from peyk.platform import clipboard, opener


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
