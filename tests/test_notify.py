"""The thread that talks to Windows. Nothing is really shown (see conftest)."""

import pytest

from peyk.platform import notify
from peyk.platform.notify import SENDER, Toaster


@pytest.fixture
def toaster():
    made = Toaster()
    yield made
    made.close()


def wait(toaster):
    toaster._thread.submit(int).result(5)


def test_one_per_chat(toaster, windows):
    toaster.show(5, "Sara", "hello")
    toaster.show(5, "Sara (2)", "again", silent=True)
    toaster.show(-20, "Group", "hi")
    wait(toaster)
    assert windows.senders == [("Peyk", SENDER)] and windows.registered
    assert [(t.tag, t.group, t.text_fields) for t in windows.shown] == [
        ("5", "chats", ["Sara", "hello"]),
        ("5", "chats", ["Sara (2)", "again"]),
        ("-20", "chats", ["Group", "hi"]),
    ]
    assert windows.shown[0].audio is None and windows.shown[1].audio.silent
    assert windows.threads == {"notify_0"}


def test_forgetting(toaster, windows):
    toaster.forget(5)  # nothing was ever shown: no thread is started for this
    assert toaster._thread is None
    toaster.show(5, "Sara", "hello")
    toaster.forget(5)
    toaster.forget(5)
    toaster.forget(6)
    wait(toaster)
    assert [t.tag for t in windows.removed] == ["5"]


def test_clicks(toaster, windows):
    clicked = []
    toaster.on_click = clicked.append
    toaster.show(5, "Sara", "hello")
    wait(toaster)
    windows.shown[0].on_activated(None)
    assert clicked == [5]
    toaster.forget(5)  # Windows has removed it already
    wait(toaster)
    assert windows.removed == []
    toaster.close()
    windows.shown[0].on_activated(None)
    assert clicked == [5]


def test_the_end(windows):
    toaster = Toaster()
    toaster.show(5, "Sara", "hello")
    toaster.close()
    assert windows.cleared == 1 and windows.registered
    toaster.show(5, "Sara", "too late")
    toaster.close()
    assert len(windows.shown) == 1


def test_logging_out_leaves_no_trace(toaster, windows):
    toaster.show(5, "Sara", "hello")
    toaster.clear(for_good=True)
    wait(toaster)
    assert windows.cleared == 1 and not windows.registered
    toaster.show(5, "Sara", "signed in again")
    wait(toaster)
    assert windows.registered and len(windows.shown) == 2


def test_leftovers_of_an_earlier_run(windows, monkeypatch):
    endings = []
    monkeypatch.setattr(notify, "on_window_closed", endings.append)
    toaster = Toaster()
    toaster.start()
    wait(toaster)
    assert windows.cleared == 0 and not windows.registered  # never used before
    windows.registered = True
    toaster.start()
    wait(toaster)
    assert windows.cleared == 1
    endings[0]()  # the window is closed
    assert windows.cleared == 2
    toaster.close()


def test_trouble_at_the_start_turns_them_off(toaster, windows):
    windows.trouble = OSError("no notifications here")
    toaster.show(5, "Sara", "hello")
    wait(toaster)
    windows.trouble = None
    toaster.show(5, "Sara", "hello")
    wait(toaster)
    assert windows.shown == []


def test_trouble_later_does_not(toaster, windows):
    toaster.show(5, "Sara", "hello")
    wait(toaster)
    windows.trouble = OSError("Element not found")
    toaster.forget(5)
    wait(toaster)
    windows.trouble = None
    toaster.show(5, "Sara", "again")
    wait(toaster)
    assert len(windows.shown) == 2


def test_raising_a_window_that_is_gone():
    notify.bring_to_front(0)
    notify.bring_to_front(0x7FFFFFF0)
