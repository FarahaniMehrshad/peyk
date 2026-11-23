"""Forwarding, pinned messages and managing chats."""

import time
from dataclasses import replace

import pytest

from driver import OFFLINE, run
from peyk.backend import sample
from peyk.backend.base import BackendError, ErrorKind, MessagesPinned, MessagesRemoved
from peyk.backend.fake import FakeBackend
from peyk.config import Config
from peyk.keys import resolve
from peyk.models import FOREVER
from peyk.store import ARCHIVE
from peyk.ui.screens.confirm import ChoiceScreen
from peyk.ui.screens.finder import ChatFinder
from peyk.ui.widgets.pin_bar import PinBar
from peyk.ui.widgets.status import StatusBar

SAVED, BOT, DANA = 1, sample.BOT, sample.HEBREW
FORBIDDEN = BackendError(ErrorKind.REFUSED, "This chat does not allow forwarding.")
ADMINS = BackendError(ErrorKind.REFUSED, "Only admins of this chat can do that.")


def notice(d):
    return d.screen.query_one(StatusBar).notice


def calls(d, name):
    return [argument for found, argument in d.backend.calls if found == name]


def bar(d):
    return d.screen.query_one(PinBar)


def bar_text(d):
    found = bar(d)
    return found.render_line(0).text if found.display else ""


async def select(d, message_id):
    d.talk.show_message(message_id)
    await d.settle()


# -- forwarding


async def test_forwarding_with_the_sender():
    async with run() as d:
        await d.open(sample.GROUP)
        await select(d, 101)
        await d.press("F")
        finder = d.app.screen
        assert isinstance(finder, ChatFinder) and "Forward to" in d.text()
        assert finder.results.entries[0].value.id == SAVED  # the usual place, first
        await d.press("enter")
        assert isinstance(d.app.screen, ChoiceScreen)
        assert "Forward to Saved Messages?" in d.text() and "build is green" in d.text()
        await d.press("y")
        assert calls(d, "forward") == [f"{sample.GROUP}:101:{SAVED}:author"]
        assert notice(d) == "Forwarded to Saved Messages."
        await d.open(SAVED)
        assert "Forwarded from Reza" in "\n".join(d.said())
        assert d.screen._forward_tokens == {}


async def test_forwarding_without_the_sender():
    async with run() as d:
        await d.open(sample.GROUP)
        await select(d, 101)
        await d.press("F", *"weather", "enter", "h")
        assert calls(d, "forward") == [f"{sample.GROUP}:101:{BOT}:no author"]
        assert not d.backend.messages[BOT][-1].forwarded


@pytest.mark.parametrize("keys", [["escape"], ["enter", "n"], ["enter", "escape"]])
async def test_forwarding_cancelled(keys):
    async with run() as d:
        await d.open(sample.GROUP)
        await select(d, 101)
        await d.press("F", *keys)
        assert calls(d, "forward") == []
        assert not isinstance(d.app.screen, (ChatFinder, ChoiceScreen))


async def test_only_chats_we_can_write_in():
    backend = FakeBackend()
    news = backend.chat_map[sample.NEWS]
    backend.chat_map[sample.NEWS] = replace(news, can_write=False)
    async with run(backend=backend) as d:
        await d.open(sample.GROUP)
        await select(d, 101)
        await d.press("F")
        offered = {e.value.id for e in d.app.screen.results.entries}
        assert sample.NEWS not in offered and sample.GROUP in offered


async def test_what_cannot_be_forwarded():
    async with run() as d:
        await d.open(sample.GROUP)
        await d.press("F")
        assert notice(d) == "Select a message to forward first: j/k select."
        await select(d, 100)  # "created the group"
        await d.press("F")
        assert notice(d) == "This cannot be forwarded."


async def test_refused_and_tried_again():
    async with run() as d:
        await d.open(sample.GROUP)
        await select(d, 101)
        d.backend.fail("forward", FORBIDDEN)
        await d.press("F", "enter", "y")
        assert notice(d) == "Not forwarded. This chat does not allow forwarding."
        d.backend.fail("forward", OFFLINE)
        await d.press("F", "enter", "y")
        assert "Not forwarded. Could not reach Telegram." in notice(d)
        (token,) = d.screen._forward_tokens.values()
        await d.press("F", "enter", "y")
        assert notice(d) == "Forwarded to Saved Messages."
        assert token in d.backend.tokens  # the same one each time: never twice
        assert len(calls(d, "forward")) == 3


# -- pinned messages


async def test_the_bar_shows_the_newest_pin():
    async with run() as d:
        await d.open(sample.GROUP)
        assert bar_text(d).startswith("Pinned 1 of 2  Run this first: uv sync")
        await d.open(sample.SARA)
        assert bar_text(d).startswith("Pinned  ") and "of" not in bar_text(d)
        await d.open(BOT)
        assert bar_text(d) == "" and not bar(d).display
        assert calls(d, "pinned") == [str(sample.GROUP), str(sample.SARA), str(BOT)]
        await d.open(sample.GROUP)
        assert len(calls(d, "pinned")) == 3  # asked once a session


async def test_going_to_pinned_messages_one_after_another():
    async with run() as d:
        await d.open(sample.GROUP)
        await d.press("P")
        assert d.talk.selected == 102
        assert bar_text(d).startswith("Pinned 2 of 2  Hey everyone")
        await d.press("P")
        assert d.talk.selected == 101
        assert bar_text(d).startswith("Pinned 1 of 2")
        await d.pilot.click(PinBar)
        await d.settle()
        assert d.talk.selected == 102


async def test_no_pins():
    async with run() as d:
        await d.press("P")
        assert notice(d) == "Open a chat first."
        await d.open(BOT)
        await d.press("P")
        assert notice(d) == "No pinned messages here."


@pytest.mark.parametrize(("chat", "answer", "how"), [
    (sample.GROUP, "y", ""),
    (sample.GROUP, "s", ":silent"),
    (DANA, "y", ""),
    (DANA, "m", ":only me"),
])  # fmt: skip
async def test_pinning(chat, answer, how):
    async with run() as d:
        await d.open(chat)
        newest = d.talk.messages[-1]
        await select(d, newest.id)
        await d.press(":", *"pin", "enter")
        assert "Pin this message?" in d.text()
        assert ("tell the members" in d.text()) is (chat == sample.GROUP)
        await d.press(answer)
        assert calls(d, "pin") == [f"{chat}:{newest.id}{how}"]
        assert notice(d) == "Pinned."
        assert bar(d).message.id == newest.id


async def test_pinning_in_saved_messages_asks_nothing():
    async with run() as d:
        await d.open(SAVED)
        await select(d, d.talk.messages[-1].id)
        await d.press(":", *"pin", "enter")
        assert calls(d, "pin") and not isinstance(d.app.screen, ChoiceScreen)


async def test_unpinning():
    async with run() as d:
        await d.open(sample.GROUP)
        await select(d, 103)
        await d.press(":", *"unpin", "enter")
        assert notice(d) == "That message is not pinned."
        await select(d, 101)
        await d.press(":", *"unpin", "enter")
        assert calls(d, "unpin") == [f"{sample.GROUP}:101"]
        assert bar_text(d).startswith("Pinned  Run this first")
        d.talk.deselect()
        await d.press(":", *"unpin", "enter")  # the one in the bar
        assert calls(d, "unpin")[-1] == f"{sample.GROUP}:102"
        assert not bar(d).display and notice(d) == "Unpinned."


async def test_pinning_refused():
    async with run() as d:
        await d.open(sample.GROUP)
        await select(d, 103)
        d.backend.fail("pin", ADMINS)
        await d.press(":", *"pin", "enter", "y")
        assert notice(d) == "Not pinned. Only admins of this chat can do that."
        assert bar_text(d).startswith("Pinned 1 of 2")


async def test_pins_from_elsewhere():
    async with run() as d:
        await d.open(sample.GROUP)
        d.backend.emit(MessagesPinned(sample.GROUP, (102,), False))
        await d.settle()
        assert bar_text(d).startswith("Pinned  Hey everyone")
        d.backend.emit(MessagesPinned(sample.GROUP, (104,), True))  # a message we have
        await d.settle()
        assert bar_text(d).startswith("Pinned 1 of 2") and bar(d).message.id == 104
        assert len(calls(d, "pinned")) == 1
        d.backend.pins[sample.GROUP] = [101, 104, 5]
        d.backend.emit(MessagesPinned(sample.GROUP, (5,), True))  # one we do not have
        await d.settle()
        assert len(calls(d, "pinned")) == 2
        d.backend.emit(MessagesRemoved(sample.GROUP, (104,)))
        await d.settle()
        assert bar(d).message.id == 101


# -- managing chats


def menu_text(d):
    return d.text() if isinstance(d.app.screen, ChoiceScreen) else ""


async def test_the_menu_fits_the_chat():
    async with run() as d:
        await d.press("c")  # Sara, selected in the list
        text = menu_text(d)
        for line in ("m: mute...", "a: archive", "p: unpin from the top", "r: mark as read"):
            assert line in text
        await d.press("escape", "j", "j", "j", "c")  # Dana: read, not pinned
        assert "p: pin to the top" in menu_text(d) and "u: mark as unread" in menu_text(d)
        await d.press("escape")
        await d.open(SAVED)
        await d.press("c")
        assert "archive" not in menu_text(d)
        await d.press("n")
        assert not isinstance(d.app.screen, ChoiceScreen)


@pytest.mark.parametrize(("key", "said", "hours"), [
    ("1", "Muted for 1 hour.", 1), ("8", "Muted for 8 hours.", 8), ("2", "Muted for 2 days.", 48),
])  # fmt: skip
async def test_muting_for_a_while(key, said, hours):
    async with run() as d:
        await d.press("c", "m")
        assert "Mute" in d.text() and "f: until you unmute it" in d.text()
        await d.press(key)
        assert notice(d) == said
        until = d.screen.store.chats[sample.SARA].muted_until
        assert abs(until - (d.backend.chat_map[sample.SARA].muted_until)) < 1
        assert abs(until - time.time() - hours * 3600) < 60
        await d.press("c")
        assert "m: unmute" in menu_text(d)
        await d.press("m")
        assert notice(d) == "Unmuted." and d.screen.store.chats[sample.SARA].muted_until == 0


async def test_muting_for_good():
    async with run() as d:
        await d.press("c", "m", "f")
        assert notice(d) == "Muted until you unmute it."
        assert d.screen.store.chats[sample.SARA].muted_until == FOREVER


async def test_archiving_and_back():
    async with run() as d:
        await d.press("c", "a")
        assert notice(d) == "Archived."
        assert sample.SARA not in [c.id for c in d.screen.store.visible(0)]
        assert calls(d, "archive") == [str(sample.SARA)]
        d.screen._choose_folder(ARCHIVE)
        await d.settle()
        assert d.screen.chat_list.selected == sample.SARA
        await d.press("c")
        assert "a: move out of the archive" in menu_text(d)
        await d.press("a")
        assert notice(d) == "Moved out of the archive."
        assert calls(d, "unarchive") == [str(sample.SARA)]


async def test_pinning_chats_to_the_top():
    async with run() as d:
        await d.open(DANA)
        await d.press("c", "p")
        assert notice(d) == "Pinned to the top."
        await d.settle(0.7)  # the list is asked for again
        assert notice(d) == "Pinned to the top."  # which leaves the notice alone
        assert d.screen.store.chats[DANA].pin == 1 and d.screen.store.chats[sample.SARA].pin == 2
        assert d.screen.store.visible(0)[0].id == DANA
        await d.press("c", "p")
        assert notice(d) == "Unpinned from the top."
        await d.settle(0.7)
        assert d.screen.store.chats[DANA].pin == 0


async def test_too_many_pinned_chats():
    async with run() as d:
        d.backend.most_pinned_chats = 1
        await d.open(DANA)
        await d.press("c", "p")
        assert notice(d).startswith("Not done. You have pinned as many chats")
        assert d.screen.store.chats[DANA].pin == 0


async def test_marking_unread_and_read():
    async with run() as d:
        await d.open(DANA)
        await d.press("c", "u")
        assert notice(d) == "Marked as unread."
        assert d.screen.store.chats[DANA].marked_unread
        await d.press("c")
        assert "r: mark as read" in menu_text(d)
        await d.press("r")
        assert notice(d) == "Marked as read."
        assert not d.screen.store.chats[DANA].marked_unread
        assert calls(d, "mark_not_unread") == [str(DANA)]


async def test_marking_read_from_the_list():
    async with run() as d:
        await d.press("j", "c", "r")  # the group, with unread messages
        chat = d.screen.store.chats[sample.GROUP]
        assert chat.unread == 0 and calls(d, "mark_read")[-1].startswith(f"{sample.GROUP}:")


async def test_failures_are_said():
    async with run() as d:
        d.backend.fail("mute", OFFLINE)
        await d.press("c", "m", "f")
        assert notice(d) == "Not done. Could not reach Telegram."
        assert d.screen.store.chats[sample.SARA].muted_until == 0


async def test_keys_help_and_commands():
    keys = resolve({"forward": "ctrl+f", "pinned": "alt+p", "chat_menu": "C"})
    async with run(config=Config(keys=keys)) as d:
        await d.press("c")
        assert not isinstance(d.app.screen, ChoiceScreen)
        await d.press("C")
        assert isinstance(d.app.screen, ChoiceScreen)
        await d.press("escape", "f1")
        text = d.text()
        assert "Ctrl+F" in text and "forward the selected message" in text
        assert "Alt+P" in text and "mute, archive, pin or mark the chat" in text
        await d.press("f1", ":", *"chat", "enter")
        assert "m: mute..." in menu_text(d)
