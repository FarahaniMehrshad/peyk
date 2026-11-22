"""The main screen, driven by keys against made-up chats."""

from dataclasses import replace

from driver import OFFLINE, run
from peyk.backend import sample
from peyk.backend.base import (
    BackendError,
    ErrorKind,
    FoldersChanged,
    Link,
    MessageChanged,
    MessagesRemoved,
    OutboxRead,
    PinsChanged,
)
from peyk.backend.fake import FakeBackend
from peyk.models import Folder
from peyk.store import ALL, ARCHIVE
from peyk.ui.screens import main as main_module
from peyk.ui.widgets.conversation import Conversation
from peyk.ui.widgets.status import StatusBar
from peyk.ui.widgets.tabs import TabBar

# -- chat list and folders


async def test_chats_load_in_order():
    async with run() as d:
        shown = [c.id for c in d.screen.chat_list.chats]
        assert shown[:3] == [sample.SARA, sample.GROUP, sample.NEWS]
        assert sample.OLD not in shown
        assert d.screen.chat_list.selected == sample.SARA
        assert "Peyk Developers" in d.text()


async def test_moving_in_the_list():
    async with run() as d:
        await d.press("j", "j")
        assert d.screen.chat_list.selected == sample.NEWS
        await d.press("k")
        assert d.screen.chat_list.selected == sample.GROUP
        await d.press("G")
        assert d.screen.chat_list.selected == sample.MUTED
        await d.press("j")
        assert d.screen.chat_list.selected == sample.MUTED
        await d.press("K", "up")
        assert d.screen.chat_list.selected == sample.SARA


async def test_folders_by_number():
    async with run() as d:
        await d.press("2")
        assert {c.id for c in d.screen.chat_list.chats} == {
            sample.GROUP, sample.LONG, sample.BIG, sample.PHOTOS,
        }  # fmt: skip
        await d.press("4")
        assert d.screen.chat_list.chats == [] and "No chats here." in d.text()
        await d.press("5")
        assert d.screen.folder == ARCHIVE
        assert [c.id for c in d.screen.chat_list.chats] == [sample.OLD]
        await d.press("9")  # no such folder
        assert d.screen.folder == ARCHIVE
        await d.press("0")
        assert d.screen.folder == ALL


async def test_selection_follows_the_chat_when_order_changes():
    async with run() as d:
        await d.press("j", "j")
        d.backend.receive(sample.MUTED, "now first")
        await d.settle()
        assert d.screen.chat_list.selected == sample.NEWS
        assert d.screen.chat_list.chats[1].id == sample.MUTED  # below the pinned one


async def test_long_list_scrolls():
    backend = FakeBackend()
    for i in range(500):
        base = backend.chat_map[sample.BOT]
        backend.chat_map[1000 + i] = replace(base, id=1000 + i, title=f"Chat {i}")
    async with run(size=(100, 20), backend=backend) as d:
        await d.press("G")
        assert d.screen.chat_list.selected in {c.id for c in d.screen.chat_list.chats[-1:]}
        assert d.screen.chat_list._top > 400


# -- opening and tabs


async def test_open_marks_read_and_shows_the_unread_line():
    async with run() as d:
        await d.press("enter")
        assert d.screen.active == sample.SARA and d.screen.in_chat
        assert "Unread" in d.text()
        await d.settle(0.2)
        assert ("mark_read", f"{sample.SARA}:107") in d.backend.calls
        assert d.screen.store.chats[sample.SARA].unread == 0


async def test_nothing_is_marked_read_without_focus():
    async with run() as d:
        d.hold_reading()
        await d.open(sample.SARA)
        await d.settle(0.2)
        assert not [c for c in d.backend.calls if c[0] == "mark_read"]
        d.backend.receive(sample.SARA, "while you look elsewhere")
        await d.settle(0.2)
        assert not [c for c in d.backend.calls if c[0] == "mark_read"]
        d.app.app_focus = True
        await d.settle(0.2)
        assert [c for c in d.backend.calls if c[0] == "mark_read"]


async def test_nothing_is_marked_read_behind_a_dialog():
    async with run() as d:
        await d.press("enter")
        await d.settle(0.2)
        await d.press("L")
        d.backend.calls.clear()
        d.backend.receive(sample.SARA, "behind the dialog")
        await d.settle(0.2)
        assert not [c for c in d.backend.calls if c[0] == "mark_read"]
        await d.press("n")
        await d.settle(0.2)
        assert [c for c in d.backend.calls if c[0] == "mark_read"]


async def test_only_what_was_seen_is_marked_read():
    async with run(size=(120, 16)) as d:
        await d.open(sample.BIG)
        await d.settle(0.2)
        marked = [int(a.split(":")[1]) for n, a in d.backend.calls if n == "mark_read"]
        assert not [a for n, a in d.backend.calls if a.endswith(":mentions")]
        newest = d.backend.messages[sample.BIG][-1].id
        assert marked and max(marked) < newest
        assert d.screen.store.chats[sample.BIG].unread == newest - max(marked)
        await d.press("G")
        await d.settle(0.2)
        assert d.screen.store.chats[sample.BIG].unread == 0


async def test_seeing_everything_clears_mentions():
    async with run() as d:
        await d.open(sample.GROUP)
        await d.settle(0.2)
        assert [a for n, a in d.backend.calls if a.endswith(":mentions")]
        assert d.screen.store.chats[sample.GROUP].mentions == 0


async def test_tabs():
    async with run() as d:
        await d.open(sample.SARA)
        await d.open(sample.GROUP)
        await d.open(sample.NEWS)
        assert d.screen.open_chats == [sample.SARA, sample.GROUP, sample.NEWS]
        await d.press("1")
        assert d.screen.active == sample.SARA
        await d.press("tab")
        assert d.screen.active == sample.GROUP
        await d.press("shift+tab", "shift+tab")
        assert d.screen.active == sample.NEWS
        await d.press("x")
        assert d.screen.active == sample.GROUP
        await d.open(sample.BOT)
        await d.press("X")
        assert d.screen.open_chats == [sample.BOT]
        await d.press("x")
        assert d.screen.active is None and not d.screen.in_chat
        assert "Choose a chat" in d.text()


async def test_no_more_than_nine_tabs():
    async with run() as d:
        for chat in list(d.screen.store.chats):
            await d.open(chat)
        assert len(d.screen.open_chats) == 9
        assert d.screen.active == d.screen.open_chats[-1]


async def test_focus_and_fullscreen():
    async with run() as d:
        await d.press("l")  # nothing open yet
        assert not d.screen.in_chat
        await d.press("f")
        assert not d.screen.fullscreen
        await d.press("enter", "f")
        assert not d.screen.query_one("#sidebar").display
        await d.press("h")
        assert d.screen.query_one("#sidebar").display and not d.screen.in_chat
        await d.press("l")
        assert d.screen.in_chat


async def test_narrow_window_shows_one_side():
    async with run(size=(50, 20)) as d:
        assert not d.screen.query_one("#conversation").display
        await d.press("enter")
        assert not d.screen.query_one("#sidebar").display
        await d.press("h")
        assert d.screen.query_one("#sidebar").display


async def test_persian_layout_drives_everything(monkeypatch):
    keys = {"ت": "j", "ن": "k", "ا": "h", "م": "l", "ب": "f", "ط": "x"}
    monkeypatch.setattr("peyk.ui.keymap.to_us_key", lambda c: keys.get(c, c))
    async with run() as d:
        await d.press("ت")
        assert d.screen.chat_list.selected == sample.GROUP
        await d.press("enter", "ن")
        assert d.talk.selected is not None
        await d.press("ا")
        assert not d.screen.in_chat


# -- conversation


async def test_selection_walks_messages():
    async with run() as d:
        await d.open(sample.SARA)
        ids = [m.id for m in d.backend.messages[sample.SARA]]
        await d.press("k")
        assert d.talk.selected == ids[-1]
        await d.press("k", "k")
        assert d.talk.selected == ids[-3]
        await d.press("j")
        assert d.talk.selected == ids[-2]
        await d.press("escape")
        assert d.talk.selected is None
        await d.press("k", "enter")
        assert d.talk.selected is None


async def test_older_messages_load_without_moving_the_view():
    async with run() as d:
        await d.open(sample.LONG)
        history = d.screen.store.history(sample.LONG)
        assert len(history.messages) == 50
        for _ in range(4):
            await d.press("K")
        before = d.said()
        d.screen._need_older(Conversation.NeedOlder(sample.LONG))
        await d.settle()
        assert len(history.messages) >= 100
        assert d.said() == before
        for _ in range(120):
            d.talk.scroll_lines(40)
            await d.settle()
            if history.complete and d.talk.lines_up >= d.talk._max_lines_up:
                break
        assert history.complete and history.messages[0].id == 100
        assert "Message 1." in d.text()


async def test_new_message_at_the_bottom_scrolls_in():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("G")
        d.backend.receive(sample.SARA, "fresh news")
        await d.settle()
        assert "fresh news" in d.text()


async def test_new_message_does_not_move_a_reader():
    async with run() as d:
        await d.open(sample.LONG)
        await d.press("K", "K")
        before = d.said()
        d.backend.receive(sample.LONG, "you should not see this yet")
        await d.settle()
        assert d.said() == before
        await d.press("G")
        assert "you should not see this yet" in " ".join(d.said())


async def test_edits_deletions_and_ticks():
    async with run() as d:
        await d.open(sample.GROUP)
        await d.press("G")
        mine = next(m for m in d.backend.messages[sample.GROUP] if m.text == "I'll be there.")
        d.backend.emit(MessageChanged(replace(mine, text="Maybe later.", edited=True)))
        await d.settle()
        assert "Maybe later." in d.text() and "I'll be there." not in d.text()
        d.backend.emit(MessagesRemoved(sample.GROUP, (mine.id,)))
        await d.settle()
        assert "Maybe later." not in d.text()


async def test_read_ticks_appear():
    async with run() as d:
        await d.open(sample.SARA)
        sent = d.backend.receive(sample.SARA, "mine", sample.ME, "You")
        await d.settle()
        line = next(line for line in d.said() if "mine" in line)
        assert "✓" in line and "✓✓" not in line
        d.backend.emit(OutboxRead(sample.SARA, sent.id))
        await d.settle()
        assert "✓✓" in next(line for line in d.said() if "mine" in line)


async def test_deleting_the_selected_message_clears_the_selection():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k")
        gone = d.talk.selected
        d.backend.emit(MessagesRemoved(None, (gone,)))
        await d.settle()
        assert d.talk.selected is None


async def test_spoilers_open_when_selected():
    async with run() as d:
        await d.open(sample.GROUP)
        assert "a secret" not in d.text() and "▒" in d.text()
        target = next(m for m in d.backend.messages[sample.GROUP] if "secret" in m.text)
        d.talk._select(target.id)
        await d.settle()
        assert "a secret" in d.text()


async def test_very_long_message_can_be_read_to_the_end():
    async with run(size=(100, 14)) as d:
        await d.open(sample.SARA)
        d.backend.receive(sample.SARA, "\n".join(f"line {i}" for i in range(60)))
        await d.settle()
        await d.press("G", "k")
        seen = set()
        for _ in range(30):
            seen |= {i for i in range(60) if f"line {i} " in d.text() + " "}
            await d.press("k")
        assert seen == set(range(60))


async def test_everything_wraps_at_any_width():
    async with run(size=(160, 40)) as d:
        for chat in (sample.SARA, sample.GROUP, sample.LONG):
            await d.open(chat)
            for width in (160, 120, 90, 72, 71, 45, 30, 20):
                await d.pilot.resize_terminal(width, 24)
                await d.settle()
                assert all(len(line) <= width * 2 for line in d.lines())


async def test_hostile_message_cannot_reach_the_terminal():
    from peyk.text.sanitize import sanitize

    async with run() as d:
        await d.open(sample.SARA)
        d.backend.receive(sample.SARA, sanitize("\x1b]0;x\x07 [red]not markup[/red]"))
        await d.settle()
        assert "[red]not markup[/red]" in d.text() and "\x1b" not in d.text()


# -- trouble


async def test_failed_history_can_be_retried():
    backend = FakeBackend()
    backend.fail("history", OFFLINE)
    async with run(backend=backend) as d:
        await d.press("enter")
        assert "Could not reach Telegram." in d.text()
        await d.press("h", "enter")
        assert "Could not reach" not in d.text()
        assert d.screen.store.history(sample.SARA).loaded


async def test_failed_chat_list_retries(monkeypatch):
    monkeypatch.setattr(main_module, "PAGE", 50)
    backend = FakeBackend()
    backend.fail("chats", BackendError(ErrorKind.FLOOD, "Too many requests.", 1))
    async with run(backend=backend) as d:
        await d.settle(1.2)
        assert d.screen.chat_list.chats
        assert d.screen.query_one(StatusBar).notice == ""


async def test_reconnect_fetches_what_was_missed():
    async with run() as d:
        await d.open(sample.SARA)
        await d.open(sample.GROUP)
        d.backend.set_link(Link.OFFLINE)
        d.backend.receive(sample.GROUP, "missed in the open chat", quiet=True)
        d.backend.receive(sample.SARA, "missed in another tab", quiet=True)
        d.backend.set_link(Link.ONLINE)
        await d.settle()
        await d.settle()
        await d.press("G")
        assert "missed in the open chat" in d.text()
        await d.press("1")
        assert "missed in another tab" in d.text()


async def test_chat_left_elsewhere_disappears():
    async with run() as d:
        del d.backend.chat_map[sample.BOT]
        d.backend.emit(PinsChanged())
        await d.settle(0.7)
        assert sample.BOT not in {c.id for c in d.screen.chat_list.chats}


async def test_folder_changes_are_picked_up():
    async with run() as d:
        d.backend.folder_list = [Folder(9, "Brand new", bots=True)]
        await d.press("3")
        d.backend.emit(FoldersChanged())
        await d.settle(0.7)
        titles = [t.title for t in d.screen.query_one("#folders", TabBar).tabs]
        assert titles == ["All", "Brand new", "Archive"] and d.screen.folder == ALL


async def test_late_reply_for_a_chat_that_is_no_longer_shown():
    backend = FakeBackend()
    async with run(backend=backend) as d:
        backend.delay = 0.2
        d.screen.open_chat(sample.SARA)
        await d.pilot.pause(0.05)
        d.screen.open_chat(sample.BOT)
        await d.settle(0.6)
        assert d.talk.chat.id == sample.BOT
        assert "Sunny" in d.text()
        assert d.screen.store.history(sample.SARA).loaded


async def test_closing_while_loading():
    backend = FakeBackend()
    async with run(backend=backend) as d:
        backend.delay = 0.2
        d.screen.open_chat(sample.SARA)
        await d.pilot.pause(0.05)
        await d.pilot.press("x")
        await d.settle(0.5)
        assert d.screen.active is None and "Choose a chat" in d.text()
