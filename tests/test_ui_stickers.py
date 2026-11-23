"""Sending stickers and GIFs from one's own collection."""

from dataclasses import replace

import pytest

from driver import OFFLINE, run
from peyk.backend import sample
from peyk.backend.base import BackendError, ErrorKind
from peyk.backend.fake import FakeBackend
from peyk.config import Config
from peyk.keys import resolve
from peyk.models import State
from peyk.platform.graphics import Graphics
from peyk.ui.screens.stickers import NO_GIFS, NO_STICKERS, PackList, StickerGrid, StickerPicker
from peyk.ui.widgets import sixels
from peyk.ui.widgets.status import StatusBar

SIXEL = Graphics(sixel=True, cell=(10, 20), half=False)
REFUSED = BackendError(ErrorKind.REFUSED, "This chat does not allow stickers.")


def notice(d):
    return d.screen.query_one(StatusBar).notice


def calls(d, name):
    return [argument for found, argument in d.backend.calls if found == name]


def grid(d):
    return d.app.screen.query_one(StickerGrid)


def name_line(d):
    return str(d.app.screen.query_one("#sticker-name").render())


def last(d):
    return d.talk.messages[-1]


async def test_packs_in_order_and_cancelling():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("s")
        assert isinstance(d.app.screen, StickerPicker)
        text = d.text()
        for title in ("Stickers", "Recent", "Favourites", "Sunny faces", "Hearts and stars"):
            assert title in text
        assert text.index("Recent") < text.index("Favourites") < text.index("Sunny faces")
        assert "h/j/k/l move  Tab pack  Enter send  Esc cancel" in text
        assert "😎  1 of 4  Recent" in name_line(d)
        assert calls(d, "stickers") == []  # recent ones come with the list
        await d.press("escape")
        assert not isinstance(d.app.screen, StickerPicker)
        await d.press("s", "s")  # the same key closes it
        assert not isinstance(d.app.screen, StickerPicker)
        assert calls(d, "send") == []


async def test_choosing_sends_at_once():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i", *"half", "escape", "s", "l", "enter")
        assert calls(d, "send") == [f"{sample.SARA}:"]
        sent = last(d)
        assert sent.media.id == sample.STICKER_PACKS[0].stickers[1].id
        assert sent.media.kind == "sticker" and sent.outgoing and sent.state is State.SENT
        assert "[Sticker" in "\n".join(d.said())
        assert d.screen.draft.text == "half"  # what was being written stays


async def test_moving_around_and_between_packs():
    async with run(size=(90, 30)) as d:
        await d.open(sample.SARA)
        await d.press("s", "tab", "tab")
        assert calls(d, "stickers") == ["set:1"]  # opened when it is shown
        assert "1 of 12  Sunny faces" in name_line(d)
        columns = grid(d)._columns
        await d.press("j")
        assert grid(d).index == columns
        await d.press("l", "k", "h", "h")
        assert grid(d).index == 0
        await d.press("J")  # a page down, as far as there is
        assert grid(d).index > columns
        await d.press("shift+tab")
        assert "Favourites" in name_line(d)
        for _ in range(4):
            await d.press("tab")
        assert "Recent" in name_line(d)  # round again
        await d.press("shift+tab", "enter")
        assert last(d).media.id == sample.CATS[0].id


async def test_moving_stops_at_the_edges():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("s", "h", "k")
        assert grid(d).index == 0
        for _ in range(6):
            await d.press("l")
        assert grid(d).index == 3
        await d.press("j", "J")
        assert grid(d).index == 3


async def test_the_last_pack_is_open_next_time():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("s", "tab", "tab", "tab", "escape", "s")
        assert "Hearts and stars" in name_line(d)
        await d.press("escape", "g")
        assert "GIF 1 of 7" in name_line(d)


async def test_a_reply_goes_with_it():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "r", "escape")  # stops writing; the reply stays
        reply = d.screen.draft.reply
        assert reply is not None
        await d.press("s", "enter")
        assert calls(d, "send") == [f"{sample.SARA}::{reply.id}"]
        assert last(d).reply.id == reply.id
        assert d.screen.draft.reply is None


async def test_gifs():
    async with run() as d:
        await d.open(sample.GROUP)
        await d.press("g")
        assert "GIFs" in d.text() and "Tab pack" not in d.text()
        assert "Recent" not in d.text()
        await d.press("l", "enter")
        assert last(d).media.kind == "gif" and last(d).media.id == sample.GIFS[1].id
        assert "[GIF]" in "\n".join(d.said())


async def test_from_the_list_the_selected_chat_is_opened():
    async with run() as d:
        main = d.screen
        assert main.active is None
        await d.press("j", "s")
        assert main.active == main.chat_list.selected != None  # noqa: E711
        assert isinstance(d.app.screen, StickerPicker)


async def test_where_they_cannot_be_sent():
    backend = FakeBackend()
    news = backend.chat_map[sample.NEWS]
    backend.chat_map[sample.NEWS] = replace(news, can_write=False)
    async with run(backend=backend) as d:
        await d.open(sample.NEWS)
        await d.press("l", "s")
        assert notice(d) == "You cannot write in this chat."
        assert not isinstance(d.app.screen, StickerPicker)
        await d.open(sample.SARA)
        mine = next(m for m in reversed(d.talk.messages) if m.outgoing and m.text)
        d.talk.show_message(mine.id)
        await d.press("e", "escape", "s")
        assert notice(d) == "Finish editing first."


async def test_empty_collections():
    async with run() as d:
        d.backend.sticker_list = []
        d.backend.gifs = ()
        await d.open(sample.SARA)
        await d.press("s")
        assert NO_STICKERS in d.text()
        await d.press("enter", "l", "tab")  # nothing to choose
        await d.press("escape", "g")
        assert NO_GIFS in d.text()
        await d.press("enter", "escape")
        assert calls(d, "send") == []


async def test_an_empty_pack_and_one_that_fails():
    async with run() as d:
        d.backend.sticker_list = [*sample.STICKER_PACKS[:2],
                                  replace(sample.STICKER_PACKS[2], stickers=())]  # fmt: skip
        await d.open(sample.SARA)
        await d.press("s", "tab", "tab")
        assert "This pack is empty." in name_line(d)
        await d.press("escape")
        d.backend.sticker_list = list(sample.STICKER_PACKS)
        d.backend.fail("stickers", OFFLINE)
        await d.press("s")  # where it was last time
        assert "Could not reach Telegram." in name_line(d)
        await d.press("enter")
        assert calls(d, "send") == []


async def test_offline():
    async with run() as d:
        d.backend.fail("sticker_packs", OFFLINE)
        await d.open(sample.SARA)
        await d.press("s")
        assert "Could not reach Telegram." in d.text()


async def test_refused_ones_can_be_sent_again_or_discarded():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.fail("send", REFUSED)
        await d.press("s", "enter")
        failed = last(d)
        assert failed.state is State.FAILED and failed.trouble == REFUSED.message
        assert "This chat does not allow stickers." in "\n".join(d.said())
        d.talk.show_message(failed.id)
        await d.press("e")
        assert notice(d) == "A sticker or GIF cannot be edited. Send it again or discard it."
        assert not d.screen.writing
        await d.press("enter")  # try again
        assert last(d).state is State.SENT and last(d).media.kind == "sticker"
        d.backend.fail("send", REFUSED)
        await d.press("escape", "s", "enter")
        again = last(d)
        assert again.state is State.FAILED
        d.talk.show_message(again.id)
        await d.press("d")
        assert all(m.id != again.id for m in d.talk.messages)
        assert d.screen.outbox.unsent == 0


async def test_the_mouse():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("s")
        await d.pilot.click(PackList, offset=(3, 2))  # Sunny faces
        await d.settle()
        assert "Sunny faces" in name_line(d)
        await d.pilot.click(StickerGrid, offset=(1 + 10 + 4, 2))  # the second one
        await d.settle()
        assert last(d).media.id == sample.FACES[1].id


@pytest.mark.parametrize("size", [(50, 16), (80, 24), (200, 60)])
async def test_any_window(size):
    async with run(size=size) as d:
        await d.open(sample.SARA)
        await d.press("s", "tab", "tab")
        for _ in range(8):
            await d.press("j")
            shown = grid(d)
            row = shown.index // shown._columns - shown._first
            assert 0 <= row < shown._rows_shown
        await d.press("enter")
        assert len(calls(d, "send")) == 1


async def test_pictures_are_fetched_for_what_is_in_view():
    async with run(size=(80, 24)) as d:
        await d.open(sample.SARA)
        await d.press("s", "tab", "tab")
        await d.settle(0.2)
        fetched = {int(a.split(":")[0]) for a in calls(d, "file_picture")}
        in_view = {grid(d).items[i].id for i, _, _ in grid(d)._in_view()}
        assert in_view and in_view <= fetched
        assert sample.FACES[-1].id not in fetched  # far below
        assert "▀" in d.text() or "▄" in d.text()


async def test_without_pictures_there_are_emoji():
    async with run(config=Config(inline_images=False)) as d:
        await d.open(sample.SARA)
        await d.press("s")
        assert calls(d, "file_picture") == []
        assert "😎" in d.text()
        await d.press("escape", "g")
        assert "GIF" in d.text()


async def test_sixels_where_the_stickers_are(monkeypatch):
    written = []
    monkeypatch.setattr(sixels, "write", lambda widget, data: written.append(data))
    async with run(size=(120, 40), graphics=SIXEL) as d:
        await d.open(sample.SARA)
        written.clear()
        await d.press("s")
        for _ in range(3):
            await d.settle(0.2)
        assert written and "\x1bP" in written[-1]
        x, y = grid(d).content_region.x + 1, grid(d).content_region.y
        assert f"\x1b[{y + 1};{x + 1}H" in written[-1]


async def test_own_keys():
    keys = resolve({"stickers": "S", "gifs": "ctrl+g", "focus_chat": "n"})
    async with run(config=Config(keys=keys)) as d:
        await d.open(sample.SARA)
        await d.press("s")
        assert not isinstance(d.app.screen, StickerPicker)
        await d.press("S", "n", "enter")
        assert last(d).media.id == sample.STICKER_PACKS[0].stickers[1].id
        await d.press("ctrl+g")
        assert isinstance(d.app.screen, StickerPicker)
        await d.press("escape", "f1")
        assert "S Ctrl+G" in d.text() and "send a sticker, a GIF" in d.text()


async def test_the_command_bar():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press(":", *"gif", "enter")
        assert isinstance(d.app.screen, StickerPicker) and "GIFs" in d.text()
        await d.press("escape", ":", *"sticker", "enter")
        assert "Stickers" in d.text()
