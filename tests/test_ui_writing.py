"""Writing, editing, deleting: driven by keys against made-up chats."""

import asyncio
from dataclasses import replace

import pytest
from textual import events

from driver import OFFLINE, run
from peyk.backend import sample
from peyk.backend.base import BackendError, ErrorKind, Link, MessagesRemoved
from peyk.backend.fake import FakeBackend
from peyk.config import Config
from peyk.models import Kind, Message, State
from peyk.text.bidi import ZWNJ
from peyk.ui.screens import main as main_module
from peyk.ui.screens.confirm import ChoiceScreen
from peyk.ui.screens.main import MainScreen
from peyk.ui.screens.writing import deletions
from peyk.ui.widgets.status import StatusBar

REFUSED = BackendError(ErrorKind.REFUSED, "You cannot write in this chat.")


def box(d):
    return d.screen.box


async def typed(d, text):
    for char in text:
        box(d).post_message(events.Key(char, char))
    await d.settle()


def sends(d):
    return [argument for name, argument in d.backend.calls if name == "send"]


def notice(d):
    return d.screen.query_one(StatusBar).notice


def last(d, chat_id=sample.SARA):
    return d.backend.messages[chat_id][-1]


# -- typing and sending


async def test_write_and_send():
    async with run() as d:
        await d.open(sample.SARA)
        assert not box(d).has_focus
        await d.press("i")
        assert box(d).has_focus and d.screen.query_one(StatusBar).mode == "WRITE"
        await typed(d, "hello jk /?:")
        await d.press("enter")
        assert last(d).text == "hello jk /?:" and last(d).outgoing
        assert box(d).text == "" and box(d).has_focus
        assert "hello jk /?:" in "\n".join(d.said())
        assert d.screen.store.chats[sample.SARA].last.text == "hello jk /?:"
        await d.press("escape", "k")
        assert d.talk.selected is not None and box(d).text == ""


async def test_persian_with_zwnj_is_sent_unchanged():
    text = "می" + ZWNJ + "خواهم سلام"
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        for char in text:
            key = "zero_width_non_joiner" if char == ZWNJ else char
            box(d).post_message(events.Key(key, char))
        await d.settle()
        await d.press("enter")
        assert last(d).text == text


async def test_several_lines_and_pasting():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        await typed(d, "one")
        await d.press("ctrl+j")
        box(d).post_message(events.Paste("two\r\nthree\x1b[31m"))
        await d.settle()
        assert box(d).text == "one\ntwo\nthree[31m"
        assert box(d).size.height == 3
        await d.press("enter")
        assert last(d).text == "one\ntwo\nthree[31m"
        assert box(d).size.height == 1


async def test_box_stops_growing():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        box(d).insert("\n".join(str(i) for i in range(50)))
        await d.settle()
        assert box(d).size.height == 8
        assert box(d).render_line(7).text.strip() == "49"
        await d.press("ctrl+home")
        assert box(d).render_line(0).text.strip() == "0"


async def test_formatting_is_sent_and_can_be_turned_off():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        await typed(d, "a **b**")
        await d.press("enter")
        assert last(d).text == "a b" and last(d).spans[0].kind == "bold"
    async with run(config=Config(formatting=False)) as d:
        await d.open(sample.SARA)
        await d.press("i")
        await typed(d, "a **b**")
        await d.press("enter")
        assert last(d).text == "a **b**" and not last(d).spans


async def test_empty_and_too_long_are_not_sent():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i", "space", "ctrl+j", "enter")
        box(d).set("")
        box(d).insert("x" * 4100)
        await d.press("enter")
        assert not sends(d) and "Too long by 4" in notice(d)
        assert "4100/4096" in box(d).header
        box(d).insert("\U0001f44d")  # counts twice
        await d.press("enter")
        assert "Too long by 6" in notice(d)


async def test_each_chat_has_its_own_draft():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        await typed(d, "for sara")
        await d.open(sample.BOT)
        assert box(d).text == ""
        await typed(d, "for bot")
        await d.open(sample.SARA)
        assert box(d).text == "for sara"
        await d.open(sample.BOT)
        assert box(d).text == "for bot"


async def test_channel_cannot_be_written_to():
    backend = FakeBackend()
    chat = backend.chat_map[sample.NEWS]
    backend.chat_map[sample.NEWS] = replace(chat, can_write=False)
    async with run(backend=backend) as d:
        await d.open(sample.NEWS)
        assert "You cannot write in this chat." in d.text()
        await d.press("i")
        assert not box(d).has_focus and "cannot write" in notice(d)
        await d.press("k", "r")
        assert not d.screen.draft.reply
        await d.pilot.click("#compose")
        assert not box(d).has_focus


async def test_write_from_the_list_opens_that_chat():
    async with run() as d:
        await d.press("j", "i")
        assert d.screen.active == sample.GROUP and box(d).has_focus
    async with run() as d:
        await d.press("escape", "r", "e", "d", "y")
        assert d.screen.active is None


async def test_clicking_the_box_starts_writing():
    async with run() as d:
        await d.open(sample.SARA)
        await d.pilot.click("#compose")
        await d.settle()
        assert box(d).has_focus and d.screen.in_chat


async def test_keys_that_still_work_while_writing():
    async with run() as d:
        await d.open(sample.SARA)
        await d.open(sample.LONG)
        await d.press("i", "pageup")
        assert d.talk.lines_up == 10
        await d.press("tab")
        assert d.screen.active == sample.SARA and box(d).has_focus
        await d.press("f1")
        assert not isinstance(d.app.screen, MainScreen)
        await d.press("escape")
        assert box(d).has_focus


async def test_alt_keys_are_not_commands():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        box(d).post_message(events.Key("alt+x", "x"))
        await d.settle()
        assert d.screen.active == sample.SARA and box(d).text == ""


async def test_small_window():
    async with run(size=(50, 12)) as d:
        await d.open(sample.SARA)
        await d.press("i")
        box(d).insert("\n" * 20)
        await d.settle()
        assert d.talk.size.height >= 2


# -- trouble while sending


async def test_message_shows_at_once_and_is_confirmed_later():
    backend = FakeBackend()
    async with run(backend=backend) as d:
        await d.open(sample.SARA)
        backend.delay = 1.0
        await d.press_now("i")
        box(d).insert("on its way")
        await d.press_now("enter")
        await d.pilot.pause(0.05)
        (pending,) = d.screen.outbox.waiting(sample.SARA)
        assert pending.state is State.SENDING
        assert any("on its way" in line and line.rstrip().endswith("…") for line in d.said())
        await d.settle(1.0)
        assert not d.screen.outbox.unsent
        assert sum("on its way" in line for line in d.said()) == 1
        assert any("on its way" in line and line.rstrip().endswith("✓") for line in d.said())


async def test_messages_go_out_in_order():
    backend = FakeBackend()
    async with run(backend=backend) as d:
        await d.open(sample.SARA)
        backend.delay = 0.1
        await d.press_now("i")
        for word in ("one", "two", "three"):
            box(d).insert(word)
            await d.press_now("enter")
        await d.settle(1.0)
        assert sends(d) == [f"{sample.SARA}:{w}" for w in ("one", "two", "three")]


async def test_offline_message_waits_for_the_connection():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.fail("send", OFFLINE)
        await d.press("i")
        await typed(d, "later")
        await d.press("enter")
        assert "Waiting for the connection" in d.text()
        await typed(d, "second")
        await d.press("enter")
        assert len(sends(d)) == 1  # it waits its turn
        d.backend.set_link(Link.OFFLINE)
        d.backend.set_link(Link.ONLINE)
        await d.settle()
        await d.settle()
        assert [m.text for m in d.backend.messages[sample.SARA][-2:]] == ["later", "second"]
        assert not d.screen.outbox.unsent


async def test_lost_confirmation_does_not_send_twice():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.lose_next_reply = True
        await d.press("i")
        await typed(d, "only once")
        await d.press("enter")
        assert d.screen.outbox.unsent == 1
        d.backend.set_link(Link.CONNECTING)
        d.backend.set_link(Link.ONLINE)
        await d.settle()
        await d.settle()
        texts = [m.text for m in d.backend.messages[sample.SARA]]
        assert texts.count("only once") == 1
        assert sum("only once" in line for line in d.said()) == 1
        assert not d.screen.outbox.unsent


async def test_refused_message_can_be_retried_edited_or_discarded():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.fail("send", REFUSED, REFUSED, REFUSED)
        await d.press("i")
        await typed(d, "a **b**")
        await d.press("enter")
        assert "You cannot write in this chat." in d.text()
        await d.press("escape", "k")
        assert d.talk.selected_message.state is State.FAILED
        assert "Enter try again" in d.text()
        await d.press("enter")  # fails again
        assert d.screen.outbox.unsent == 1
        await d.press("k", "r")
        assert "not sent yet" in notice(d)
        await d.press("e")
        assert box(d).text == "a **b**" and not d.screen.outbox.unsent
        await d.press("enter")  # and again
        assert d.screen.outbox.unsent == 1
        await d.press("escape", "k", "d")
        assert not d.screen.outbox.unsent
        assert "a b" not in [m.text for m in d.backend.messages[sample.SARA]]


async def test_taking_back_does_not_overwrite_a_draft():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.fail("send", REFUSED)
        await d.press("i")
        await typed(d, "failed")
        await d.press("enter")
        await typed(d, "draft")
        await d.press("escape", "k", "e")
        assert box(d).text == "draft" and d.screen.outbox.unsent == 1


async def test_a_message_in_flight_cannot_be_discarded():
    backend = FakeBackend()
    async with run(backend=backend) as d:
        await d.open(sample.SARA)
        backend.delay = 1.0
        await d.press_now("i")
        box(d).insert("too late")
        await d.press_now("enter", "escape", "k", "d")
        assert "being sent" in notice(d)
        await d.settle(1.0)
        assert last(d).text == "too late"


async def test_slow_mode_is_waited_out():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.fail("send", BackendError(ErrorKind.FLOOD, "Slow mode.", 1))
        await d.press_now("i")
        box(d).insert("patience")
        await d.press_now("enter")
        await d.pilot.pause(0.1)
        assert "Trying again in 1s" in d.text()
        await d.settle(1.2)
        assert last(d).text == "patience"


async def test_failures_in_one_chat_do_not_hold_up_another():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.fail("send", OFFLINE)
        await d.press("i")
        await typed(d, "stuck")
        await d.press("enter")
        await d.open(sample.BOT)
        await typed(d, "free")
        await d.press("enter")
        assert last(d, sample.BOT).text == "free"
        assert d.talk.pending == []


async def test_quitting_with_unsent_things_asks():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.fail("send", OFFLINE)
        await d.press("i")
        await typed(d, "unsent")
        await d.press("enter")
        await typed(d, "draft")
        await d.press("escape", "q")
        assert isinstance(d.app.screen, ChoiceScreen)
        assert "1 unsent message and something" in d.text()
        await d.press("n")
        assert isinstance(d.app.screen, MainScreen)
        await d.press("ctrl+q")
        assert isinstance(d.app.screen, ChoiceScreen)


# -- replying


async def test_reply():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("r")
        assert "Select a message" in notice(d)
        await d.press("k", "r")
        assert box(d).has_focus and box(d).header.startswith("Reply to سارا محمدی")
        await typed(d, "yes")
        await d.press("enter")
        target = d.backend.messages[sample.SARA][-2].id
        assert sends(d) == [f"{sample.SARA}:yes:{target}"]
        assert last(d).reply.id == target and box(d).header == ""


async def test_reply_can_be_cancelled_and_survives_switching():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "r")
        await typed(d, "text")
        await d.open(sample.BOT)
        assert box(d).header == ""
        await d.open(sample.SARA)
        assert box(d).header.startswith("Reply to")
        await d.press("escape")
        assert box(d).header.startswith("Reply to")
        await d.press("escape")
        assert box(d).header == "" and box(d).text == "text"


async def test_replied_message_is_deleted_meanwhile():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "r")
        await typed(d, "text")
        d.backend.emit(MessagesRemoved(None, (d.screen.draft.reply.id,)))
        await d.settle()
        assert box(d).header == "" and box(d).text == "text"


# -- editing


async def test_edit_last_own_message():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        await typed(d, "half a draft")
        await d.press("escape", "e")
        assert box(d).header == "Edit" and box(d).text.startswith("مرسی")
        await d.press("ctrl+u")
        await typed(d, "thanks **a lot**")
        await d.press("enter")
        edited = next(m for m in d.backend.messages[sample.SARA] if m.text == "thanks a lot")
        assert edited.edited and edited.spans[0].kind == "bold"
        assert "thanks a lot" in d.text()
        assert box(d).text == "half a draft" and box(d).header == ""


async def test_edit_keeps_formatting():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        await typed(d, "a **bold** word")
        await d.press("enter", "escape", "e", "end")
        assert box(d).text == "a bold word"
        await typed(d, "!")
        await d.press("enter")
        assert last(d).text == "a bold word!"
        assert [last(d).text[s.start : s.end] for s in last(d).spans] == ["bold"]


async def test_edit_is_cancelled_with_the_draft_restored():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "r")
        await typed(d, "draft")
        await d.press("escape", "e")
        assert box(d).header == "Edit"
        await typed(d, "changes")
        await d.press("escape", "escape")
        assert box(d).text == "draft" and box(d).header.startswith("Reply to")


async def test_failed_edit_keeps_the_text():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.fail("edit", BackendError(ErrorKind.REFUSED, "Too old."))
        await d.press("e")
        await typed(d, "!")
        text = box(d).text
        await d.press("enter")
        assert box(d).text == text and box(d).header == "Edit" and "Too old." in notice(d)
        assert not box(d).frozen
        await d.press("enter")
        assert box(d).text == "" and box(d).header == ""


async def test_box_is_frozen_while_saving():
    backend = FakeBackend()
    async with run(backend=backend) as d:
        await d.open(sample.SARA)
        await d.press("e")
        backend.delay = 1.0
        before = box(d).text
        await d.press_now("x", "enter")
        await d.press_now("y", "enter")
        assert box(d).text == before + "x" and "Saving" in box(d).header
        d.screen.open_chat(sample.BOT)
        await d.settle(1.0)
        assert not box(d).frozen
        assert len([c for c in backend.calls if c[0] == "edit"]) == 1
        assert d.screen._drafts[sample.SARA].edit_id == 0


async def test_only_own_messages_can_be_edited():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "e")
        assert "only edit your own" in notice(d) and box(d).header == ""
        await d.open(sample.BOT)
        await d.press("escape", "e")
        assert "no message here to edit" in notice(d)


async def test_empty_edit_is_refused():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("e", "ctrl+u", "enter")
        assert "empty message" in notice(d)
        assert not any(c[0] == "edit" for c in d.backend.calls)


async def test_edited_message_is_deleted_meanwhile():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("e")
        await typed(d, " more")
        d.backend.emit(MessagesRemoved(None, (d.screen.draft.edit_id,)))
        await d.settle()
        assert box(d).header == "" and box(d).text.endswith(" more")


# -- deleting and copying


async def test_delete_asks_and_deletes():
    async with run() as d:
        await d.open(sample.SARA)
        target = last(d).id
        await d.press("k", "d")
        assert "Delete this message?" in d.text() and "for everyone" in d.text()
        await d.press("enter")
        assert isinstance(d.app.screen, ChoiceScreen)
        await d.press("n")
        assert last(d).id == target
        await d.press("d", "y")
        assert ("delete", f"{sample.SARA}:{target}:everyone") in d.backend.calls
        assert d.talk.history.get(target) is None
        assert d.screen.store.chats[sample.SARA].last.id == last(d).id


async def test_delete_for_me_only():
    async with run() as d:
        await d.open(sample.SARA)
        target = last(d).id
        await d.press("k", "d", "m")
        assert ("delete", f"{sample.SARA}:{target}:me") in d.backend.calls


async def test_failed_delete_is_reported():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.fail("delete", BackendError(ErrorKind.REFUSED, "No."))
        target = last(d).id
        await d.press("k", "d", "y")
        assert "Not deleted. No." in notice(d) and d.talk.history.get(target)


@pytest.mark.parametrize(
    ("kind", "chat_id", "mine", "admin", "values"),
    [
        (Kind.SAVED, 1, True, False, ["everyone"]),
        (Kind.USER, 5, True, False, ["everyone", "me"]),
        (Kind.USER, 5, False, False, ["everyone", "me"]),
        (Kind.BOT, 5, False, False, ["everyone", "me"]),
        (Kind.GROUP, -5, True, False, ["everyone", "me"]),
        (Kind.GROUP, -5, False, False, ["me"]),
        (Kind.GROUP, -5, False, True, ["everyone", "me"]),
        (Kind.GROUP, -1_000_000_000_005, True, False, ["everyone"]),
        (Kind.GROUP, -1_000_000_000_005, False, False, []),
        (Kind.GROUP, -1_000_000_000_005, False, True, ["everyone"]),
        (Kind.CHANNEL, -1_000_000_000_005, False, False, []),
    ],
)
def test_ways_to_delete(kind, chat_id, mine, admin, values):
    chat = replace(
        FakeBackend().chat_map[sample.SARA], id=chat_id, kind=kind, can_delete_any=admin
    )
    message = Message(1, chat_id, chat.last.date, "x", outgoing=mine)
    assert [a.value for a in deletions(message, chat)] == values


async def test_copy(copied):
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "y")
        assert copied == ["باشه، خداحافظ"] and "Copied" in notice(d)


async def test_notices_go_away(monkeypatch):
    monkeypatch.setattr(main_module, "NOTICE_TIME", 1)
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("d")
        assert notice(d)
        await asyncio.sleep(1.2)
        await d.settle()
        assert notice(d) == ""


# -- maps


MAPS = Config(maps={"jj": "escape", "kj": "enter", "jw": "ctrl+w"})


async def into_focus(d, text):
    """Typed into whatever has the focus, as a person would."""
    for char in text:
        d.app.focused.post_message(events.Key(char, char))
        await d.settle()


async def test_jj_stops_writing_and_leaves_no_trace():
    async with run(config=MAPS) as d:
        await d.open(sample.SARA)
        await d.press("i")
        await into_focus(d, "hajj")
        assert box(d).text == "ha" and not box(d).has_focus
        assert d.screen.query_one(StatusBar).mode != "WRITE"
        await d.press("i")
        await into_focus(d, "kkj")
        assert last(d).text == "hak" and box(d).text == ""


async def test_maps_can_stand_for_editing_keys_and_work_in_other_boxes():
    async with run(config=MAPS) as d:
        await d.open(sample.SARA)
        await d.press("i")
        await into_focus(d, "one twojw")
        assert box(d).text == "one " and box(d).has_focus
        await d.press("escape", "/")
        assert not isinstance(d.app.screen, MainScreen)
        await into_focus(d, "sajj")
        assert isinstance(d.app.screen, MainScreen)


async def test_letters_typed_slowly_stay_letters(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("peyk.ui.widgets.edit_box.monotonic", lambda: clock[0])
    async with run(config=MAPS) as d:
        await d.open(sample.SARA)
        await d.press("i")
        await into_focus(d, "j")
        clock[0] += 1.5
        await into_focus(d, "j")
        assert box(d).text == "jj" and box(d).has_focus
        await d.press("left")  # moving in between breaks the run
        await into_focus(d, "j")
        assert box(d).text == "jjj"
