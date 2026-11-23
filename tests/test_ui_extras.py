"""Reactions, typing and notifications: driven by keys against made-up chats."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from textual import events

from driver import OFFLINE, run
from peyk.backend import sample
from peyk.backend.base import (
    BackendError,
    ChatFlags,
    ErrorKind,
    InboxRead,
    Link,
    MessageChanged,
    MessagesRemoved,
    ReactionsChanged,
    Typing,
)
from peyk.backend.fake import FakeBackend
from peyk.backend.sample import reacted
from peyk.config import Config
from peyk.models import FOREVER, Kind, ReactionChoice, ReactionMenu, Span, State
from peyk.ui import alerts
from peyk.ui.app import PeykApp
from peyk.ui.screens import extras
from peyk.ui.screens.reactions import ReactionGrid, ReactionPicker, toggled
from peyk.ui.widgets.divider import Divider
from peyk.ui.widgets.status import StatusBar

REFUSED = BackendError(ErrorKind.REFUSED, "This reaction is not allowed here.")


def calls(d, name):
    return [argument for method, argument in d.backend.calls if method == name]


def notice(d):
    return d.screen.query_one(StatusBar).notice


def last(d, chat_id=sample.SARA):
    return d.backend.messages[chat_id][-1]


async def type_text(d, text):
    for char in text:
        d.screen.box.post_message(events.Key(char, char))
    await d.settle()


# -- showing reactions


async def test_reactions_are_shown_below_the_text():
    async with run(size=(120, 60)) as d:
        await d.open(sample.GROUP)
        lines = d.said()
        at = next(i for i, line in enumerate(lines) if "build is green" in line)
        assert "🎉 3  👍 2  ❤️‍🔥 1" in lines[at + 1]
        assert all(sum(s.cell_length for s in d.talk.render_line(y)) == d.talk.size.width
                   for y in range(len(lines)))  # fmt: skip


async def test_ours_stand_out():
    async with run(size=(120, 60)) as d:
        await d.open(sample.GROUP)
        at = next(i for i, line in enumerate(d.said()) if "🎉" in line)
        styles = {s.text: s.style for s in d.talk.render_line(at) if s.text.strip()}
        assert styles["👍 2"].bold and not styles["🎉 3"].bold
        assert styles["👍 2"].color != styles["🎉 3"].color


async def test_a_single_reaction_in_a_private_chat_needs_no_number():
    async with run(size=(120, 60)) as d:
        await d.open(sample.SARA)
        line = next(line for line in d.said() if "❤️" in line and "[" not in line)
        assert line.split() == ["❤️"]


async def test_many_reactions_wrap():
    async with run(size=(80, 30)) as d:
        await d.open(sample.SARA)
        many = reacted(*((c.key, 100, False) for c in sample.REACTIONS[:30]))
        d.backend.emit(ReactionsChanged(sample.SARA, last(d).id, many))
        await d.settle()
        shown = [line for line in d.said() if "100" in line]
        assert len(shown) > 2
        assert sum(line.count("100") for line in shown) == 30


async def test_changes_arrive_live_and_keep_the_place():
    async with run() as d:
        await d.open(sample.LONG)
        await d.press("K", "K", "K")
        before = d.said()
        newest = d.backend.messages[sample.LONG][-1]
        d.backend.emit(ReactionsChanged(sample.LONG, newest.id, reacted(("👍", 1, False))))
        await d.settle()
        assert d.said() == before
        await d.press("G")
        assert any("👍 1" in line for line in d.said())
        d.backend.emit(ReactionsChanged(sample.LONG, newest.id, ()))
        await d.settle()
        assert not any("👍" in line for line in d.said())


async def test_shared_updates_do_not_lose_ours():
    async with run(size=(120, 60)) as d:
        await d.open(sample.GROUP)
        message = next(m for m in d.talk.messages if m.reactions)
        shared = reacted(("👍", 5, False), ("🎉", 1, False))
        d.backend.emit(ReactionsChanged(sample.GROUP, message.id, shared, shared=True))
        await d.settle()
        mine = {r.key: (r.count, r.mine) for r in d.talk.history.get(message.id).reactions}
        assert mine == {"👍": (5, True), "🎉": (1, False)}
        edited = replace(message, text="edited", reactions=shared, reactions_shared=True)
        d.backend.emit(MessageChanged(edited))
        await d.settle()
        kept = d.talk.history.get(message.id)
        assert kept.text == "edited" and [r.key for r in kept.reactions if r.mine] == ["👍"]


async def test_reactions_to_unknown_messages_are_ignored():
    async with run() as d:
        d.backend.emit(ReactionsChanged(sample.SARA, 1, ()))
        d.backend.emit(ReactionsChanged(12345, 1, ()))
        await d.open(sample.SARA)
        d.backend.emit(ReactionsChanged(sample.SARA, 99999, reacted(("👍", 1, False))))
        await d.settle()
        assert not any("👍 1" in line for line in d.said())


async def test_the_first_selected_message_is_shown_with_its_reactions():
    async with run() as d:
        await d.open(sample.NEWS)
        for _ in range(3):
            await d.press("escape")
            d.talk.scroll_lines(1)
            await d.press("k")
            shown = [line for line in d.said() if line.startswith("▌")]
            assert len(shown) == 2 and "🔥 48" in shown[1]


# -- giving them


async def test_react_with_a_digit():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "+")
        assert isinstance(d.app.screen, ReactionPicker)
        await d.press("2")
        assert calls(d, "react") == [f"{sample.SARA}:{last(d).id}:👍"]
        assert [r.key for r in d.talk.selected_message.reactions if r.mine] == ["👍"]
        assert any(line.split()[-1:] == ["👍"] for line in d.said())


async def test_move_and_choose():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "+")
        grid = d.app.screen.query_one(ReactionGrid)
        columns = grid._columns
        await d.press("l", "l", "j")
        assert grid.index == columns + 2
        await d.press("h", "k", "left", "right", "down", "up")
        assert grid.index == 1
        assert sample.REACTIONS[1].name in d.text()
        await d.press("enter")
        assert calls(d, "react")[0].endswith(":👍")


async def test_moving_stops_at_the_edges():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "+", "h", "k")
        grid = d.app.screen.query_one(ReactionGrid)
        assert grid.index == 0
        await d.press(*["j"] * 30)
        rows = -(-len(grid.choices) // grid._columns)
        assert grid.index // grid._columns == rows - 1
        await d.press(*["l"] * 30)
        assert grid.index == len(grid.choices) - 1


async def test_choosing_ours_takes_it_back():
    async with run(size=(120, 60)) as d:
        await d.open(sample.SARA)
        photo = next(m for m in d.talk.messages if m.reactions)
        d.talk.show_message(photo.id)
        await d.press("+")
        assert "Red Heart  (take back)" in d.text()
        await d.press("enter")
        assert calls(d, "react") == [f"{sample.SARA}:{photo.id}:"]
        assert d.talk.selected_message.reactions == ()


async def test_a_new_one_replaces_ours():
    async with run(size=(120, 60)) as d:
        await d.open(sample.SARA)
        photo = next(m for m in d.talk.messages if m.reactions)
        d.talk.show_message(photo.id)
        await d.press("+", "3")
        assert [r.key for r in d.talk.selected_message.reactions] == ["🔥"]


def test_how_many_we_may_give():
    assert toggled((), "a", 1) == ("a",)
    assert toggled(("a",), "b", 1) == ("b",)
    assert toggled(("a",), "a", 1) == ()
    assert toggled(("a", "b"), "c", 3) == ("a", "b", "c")
    assert toggled(("a", "b", "c"), "d", 3) == ("b", "c", "d")
    assert toggled(("a", "b", "c"), "b", 3) == ("a", "c")
    assert toggled((), "a", 0) == ("a",)


@pytest.mark.parametrize("key", ["escape", "q", "+"])
async def test_cancelling(key):
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "+", "l", key)
        assert not isinstance(d.app.screen, ReactionPicker)
        assert calls(d, "react") == []


async def test_nothing_to_react_to():
    async with run() as d:
        await d.press("+")
        assert "Select a message to react to first: j/k select." in notice(d)
        await d.open(sample.GROUP)
        service = next(m for m in d.talk.messages if m.service)
        d.talk.show_message(service.id)
        await d.press("+")
        assert notice(d) == "You cannot react to this."
        await d.open(sample.SARA)
        d.backend.fail("send", REFUSED)
        await d.press("i")
        await type_text(d, "x")
        await d.press("enter")
        await d.press("escape")
        d.talk.show_message(d.talk.messages[-1].id)
        assert d.talk.selected_message.state is State.FAILED
        await d.press("+")
        assert notice(d) == "That message is not sent yet."


async def test_refused_or_offline():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.fail("react", REFUSED)
        await d.press("k", "+", "1")
        assert notice(d) == "No reaction given. This reaction is not allowed here."
        assert d.talk.selected_message.reactions == ()
        d.backend.fail("reaction_menu", OFFLINE)
        await d.press("+")
        assert "Could not reach Telegram." in d.text()
        await d.press("enter", "1", "l", "j")  # nothing to choose from
        assert isinstance(d.app.screen, ReactionPicker)
        await d.press("escape")


async def test_chats_without_reactions_say_so():
    async with run() as d:
        d.backend.menus[sample.SARA] = ReactionMenu()
        await d.open(sample.SARA)
        await d.press("k", "+")
        assert "Reactions are not allowed in this chat." in d.text()


async def test_a_short_menu_with_a_custom_emoji():
    async with run() as d:
        custom = ReactionChoice("custom:77", "🦊")
        d.backend.menus[sample.SARA] = ReactionMenu((custom,))
        await d.open(sample.SARA)
        await d.press("k", "+")
        assert "Custom emoji" in d.text()
        await d.press("9")  # there is no ninth
        assert isinstance(d.app.screen, ReactionPicker)
        await d.press("1")
        assert calls(d, "react")[0].endswith(":custom:77")


async def test_paid_ones_are_left_alone():
    async with run() as d:
        await d.open(sample.SARA)
        paid = reacted(("👎", 1, True))[0]
        paid = replace(paid, key="paid", label="⭐")
        d.backend.emit(ReactionsChanged(sample.SARA, last(d).id, (paid,)))
        await d.settle()
        await d.press("k", "+", "1")
        assert calls(d, "react")[0].endswith(":❤")


async def test_choosing_with_the_mouse():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "+")
        await d.pilot.click(ReactionGrid, offset=(6 + 2, 0))
        await d.settle()
        assert calls(d, "react")[0].endswith(":👍")


@pytest.mark.parametrize("size", [(60, 20), (40, 14), (200, 60)])
async def test_the_picker_fits_any_window(size):
    async with run(size=size) as d:
        await d.open(sample.SARA)
        await d.press("k", "+")
        grid = d.app.screen.query_one(ReactionGrid)
        for _ in range(12):
            await d.press("j")
            row = grid.index // grid._columns - grid._top
            assert 0 <= row < grid.size.height
        assert all(len(line) >= 1 for line in d.lines())


async def test_persian_message_in_the_picker_and_keyboard(monkeypatch):
    keys = {"م": "l"}
    monkeypatch.setattr("peyk.ui.keymap.to_us_key", lambda c: keys.get(c, c))
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("k", "+")
        d.app.screen.post_message(events.Key("م", "م"))
        await d.settle()
        assert d.app.screen.query_one(ReactionGrid).index == 1


async def test_large_groups_are_asked_for_reactions_while_in_front():
    async with run() as d:
        d.screen._look_for_reactions()  # no chat open
        await d.open(sample.SARA)
        d.screen._look_for_reactions()  # private chats send theirs
        await d.settle()
        assert calls(d, "refresh_reactions") == []
        await d.open(sample.BIG)
        newest = last(d, sample.BIG)
        await d.press("G")
        d.backend.set_reactions(newest, reacted(("🎉", 4, False)))
        d.screen._look_for_reactions()
        await d.settle()
        (asked,) = calls(d, "refresh_reactions")
        assert asked.startswith(f"{sample.BIG}:") and asked.endswith(str(newest.id))
        assert any("🎉 4" in line for line in d.said())
        d.app.app_focus = False
        d.screen._look_for_reactions()
        d.app.app_focus = True
        d.backend.set_link(Link.OFFLINE)
        d.screen._look_for_reactions()
        await d.settle()
        assert len(calls(d, "refresh_reactions")) == 1


async def test_failing_to_ask_is_not_shown():
    async with run() as d:
        await d.open(sample.BIG)
        d.backend.fail("refresh_reactions", OFFLINE)
        d.screen._look_for_reactions()
        await d.settle()
        assert notice(d) == ""


# -- others are typing


def divider(d):
    return d.screen.query_one(Divider).render_line(0).text


def sidebar(d, title):
    lines = d.lines()
    at = next(i for i, line in enumerate(lines) if title in line[:44])
    return lines[at + 1]


async def test_typing_in_a_group():
    async with run() as d:
        await d.open(sample.GROUP)
        assert set(divider(d)) == {"─"}
        d.backend.emit(Typing(sample.GROUP, 50, "typing", "Reza Karimi"))
        await d.settle()
        assert divider(d).startswith("─ Reza Karimi is typing… ─")
        assert "Reza is typing…" in sidebar(d, "Peyk Developers")
        d.backend.emit(Typing(sample.GROUP, 52, "sending a photo", "Mina K."))
        await d.settle()
        assert "Reza Karimi and Mina K. are typing…" in divider(d)
        d.backend.emit(Typing(sample.GROUP, 51, "typing", "علی"))
        await d.settle()
        assert "3 people are typing…" in divider(d)
        assert len(divider(d)) == d.talk.size.width


async def test_typing_in_a_private_chat():
    async with run() as d:
        await d.open(sample.BOT)
        d.backend.emit(Typing(sample.BOT, sample.BOT, "recording a voice message"))
        await d.settle()
        assert "Weather Bot is recording a voice message…" in divider(d)
        assert "recording a voice message…" in sidebar(d, "Weather Bot")
        assert "Sunny" not in sidebar(d, "Weather Bot")


async def test_typing_elsewhere_only_shows_in_the_list():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.emit(Typing(sample.BOT, sample.BOT))
        await d.settle()
        assert set(divider(d)) == {"─"} and "typing…" in sidebar(d, "Weather Bot")
        await d.open(sample.BOT)
        assert "Weather Bot is typing…" in divider(d)


async def test_names_come_from_what_is_loaded():
    async with run() as d:
        await d.open(sample.GROUP)
        d.backend.emit(Typing(sample.GROUP, 50))
        await d.settle()
        assert "Reza is typing…" in divider(d)
        d.backend.emit(Typing(sample.GROUP, 50, ""))
        d.backend.emit(Typing(sample.GROUP, 987))
        await d.settle()
        assert "Someone is typing…" in divider(d)


async def test_typing_ends_with_the_message_or_when_they_stop():
    async with run() as d:
        await d.open(sample.GROUP)
        d.backend.emit(Typing(sample.GROUP, 50, "typing", "Reza"))
        await d.settle()
        d.backend.receive(sample.GROUP, "done", 50, "Reza")
        await d.settle()
        assert set(divider(d)) == {"─"}
        d.backend.emit(Typing(sample.GROUP, 50, "typing", "Reza"))
        d.backend.emit(Typing(sample.GROUP, 50, ""))
        await d.settle()
        assert set(divider(d)) == {"─"} and "typing" not in sidebar(d, "Peyk Developers")


async def test_typing_passes_by_itself(monkeypatch):
    monkeypatch.setattr("peyk.store.TYPING_LASTS", 0.05)
    async with run() as d:
        await d.open(sample.GROUP)
        d.backend.emit(Typing(sample.GROUP, 50, "typing", "Reza"))
        await d.pilot.pause()
        assert "Reza" in divider(d)
        await d.settle(1.3)
        assert set(divider(d)) == {"─"} and not d.screen.store.anyone_typing
        assert d.screen._typing_timer is None


async def test_persian_names_are_drawn_in_order():
    async with run() as d:
        await d.open(sample.SARA)
        d.backend.emit(Typing(sample.SARA, sample.SARA))
        await d.settle()
        assert "سارا محمدی"[::-1].split()[0][0] in divider(d)  # reordered, then the rest
        assert divider(d).index("is typing…") > 5
        assert sum(s.cell_length for s in d.screen.query_one(Divider).render_line(0)) == (
            d.talk.size.width
        )


async def test_long_names_do_not_break_the_line():
    async with run(size=(72, 20)) as d:
        await d.open(sample.GROUP)
        d.backend.emit(Typing(sample.GROUP, 50, "typing", "N" * 200))
        await d.settle()
        assert "is typing…" in divider(d)
        assert len(divider(d)) == d.talk.size.width


async def test_showing_typing_does_not_move_anything():
    async with run() as d:
        await d.open(sample.GROUP)
        before = d.said()
        d.backend.emit(Typing(sample.GROUP, 52, "typing", "Mina K."))
        await d.settle()
        assert d.said() == before


async def test_the_line_shows_whether_we_write():
    async with run() as d:
        assert not d.screen.query_one(Divider).display
        await d.open(sample.SARA)
        assert d.screen.query_one(Divider).display and not d.screen.query_one(Divider).active
        await d.press("i")
        assert d.screen.query_one(Divider).active
        await d.press("escape")
        assert not d.screen.query_one(Divider).active
        await d.press("x")
        assert not d.screen.query_one(Divider).display


# -- we are typing


async def test_others_are_told_now_and_then(monkeypatch):
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        assert calls(d, "typing") == []
        await type_text(d, "hello there")
        assert calls(d, "typing") == [str(sample.SARA)]
        monkeypatch.setattr(extras, "TELL_EVERY", 0)
        await type_text(d, "!")
        assert len(calls(d, "typing")) == 2


async def test_after_sending_they_are_told_anew():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        await type_text(d, "a")
        await d.press("enter")
        await type_text(d, "b")
        assert len(calls(d, "typing")) == 2


async def test_when_nobody_is_told():
    async with run(config=Config(show_typing=False)) as d:
        await d.open(sample.SARA)
        await d.press("i")
        await type_text(d, "hello")
        assert calls(d, "typing") == []
    async with run() as d:
        await d.open(sample.ME)
        await d.press("i")
        await type_text(d, "note to self")
        await d.press("escape")
        await d.open(sample.NEWS)
        await d.press("i")
        await type_text(d, "post")
        await d.press("escape")
        await d.open(sample.SARA)
        await d.press("e")  # edits our last message
        await type_text(d, "x")
        await d.press("escape", "escape")
        await d.press("i")
        await type_text(d, "  ")
        d.backend.set_link(Link.OFFLINE)
        await type_text(d, "offline")
        assert calls(d, "typing") == []


async def test_drafts_put_into_the_box_are_not_typing():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("i")
        await type_text(d, "draft")
        await d.press("escape")
        await d.open(sample.BOT)
        await d.open(sample.SARA)
        assert calls(d, "typing") == [str(sample.SARA)]


async def test_failing_to_tell_is_not_shown():
    async with run() as d:
        d.backend.fail("typing", OFFLINE)
        await d.open(sample.SARA)
        await d.press("i")
        await type_text(d, "hello")
        assert notice(d) == "" and d.screen.box.text == "hello"


# -- notifications


class Notes:
    def __init__(self):
        self.on_click = None
        self.shown, self.events = {}, []

    def show(self, chat_id, title, text, silent=False):
        self.shown[chat_id] = (title, text, silent)
        self.events.append((chat_id, title, text))

    def forget(self, chat_id):
        self.shown.pop(chat_id, None)

    def clear(self, for_good=False):
        self.shown.clear()
        self.events.append("cleared for good" if for_good else "cleared")

    def close(self):
        self.shown.clear()
        self.events.append("closed")


class notified:
    def __init__(self, config=None, backend=None, notes=None):
        self.notes = notes or Notes()
        self.run = run(config=config, backend=backend)
        self.run.app = PeykApp(
            config or Config(), None, backend=self.run.backend, notifier=self.notes
        )
        self.run.context = self.run.app.run_test(size=(120, 36))

    async def __aenter__(self):
        d = await self.run.__aenter__()
        d.notes = self.notes
        return d

    async def __aexit__(self, *details):
        return await self.run.__aexit__(*details)


async def test_new_messages_are_announced():
    async with notified() as d:
        d.backend.receive(sample.SARA, "سلام\nخوبی؟", sample.SARA, "سارا محمدی")
        d.backend.receive(sample.GROUP, "Hi all", 50, "Reza")
        d.backend.receive(sample.NEWS, "Fresh news", 0, "Peyk News")
        await d.settle()
        assert d.notes.shown == {
            sample.SARA: ("سارا محمدی", "سلام خوبی؟", False),
            sample.GROUP: ("Peyk Developers", "Reza: Hi all", False),
            sample.NEWS: ("Peyk News", "Fresh news", False),
        }


async def test_what_is_not_announced():
    async with notified() as d:
        d.backend.receive(sample.MUTED, "ping", sample.MUTED, "Noisy Nick")
        d.backend.receive(sample.MUTED, "@me", sample.MUTED, "Noisy Nick", mentioned=True)
        d.backend.receive(sample.GROUP, "me, elsewhere", sample.ME, "You")
        d.backend.receive(sample.GROUP, "joined", 51, "Ali", service=True)
        old = d.backend.receive(sample.SARA, "old", quiet=True)
        from peyk.backend.base import MessageAdded

        stale = replace(old, date=datetime.now().astimezone() - timedelta(minutes=30))
        d.backend.emit(MessageAdded(stale))
        read = replace(d.backend.messages[sample.SARA][0], text="read long ago")
        d.backend.emit(MessageAdded(read))
        d.backend.emit(ReactionsChanged(sample.SARA, old.id, reacted(("👍", 1, False))))
        d.backend.emit(Typing(sample.SARA, sample.SARA))
        await d.settle()
        assert d.notes.events == []


async def test_muting_and_unmuting_take_effect():
    async with notified() as d:
        d.backend.emit(ChatFlags(sample.SARA, muted_until=FOREVER))
        d.backend.emit(ChatFlags(sample.MUTED, muted_until=0.0))
        d.backend.receive(sample.SARA, "x")
        d.backend.receive(sample.MUTED, "y")
        await d.settle()
        assert list(d.notes.shown) == [sample.MUTED]


async def test_the_chat_in_view_is_not_announced():
    async with notified() as d:
        await d.open(sample.SARA)
        d.backend.receive(sample.SARA, "seen")
        await d.settle()
        assert d.notes.events == []
        d.backend.receive(sample.BOT, "another chat")
        await d.settle()
        assert list(d.notes.shown) == [sample.BOT]
        d.app.app_focus = False
        d.backend.receive(sample.SARA, "not seen")
        await d.settle()
        assert sample.SARA in d.notes.shown


async def test_reading_takes_the_notification_away():
    async with notified() as d:
        await d.open(sample.SARA)
        d.app.app_focus = False
        message = d.backend.receive(sample.SARA, "hello")
        d.backend.receive(sample.BOT, "hello")
        await d.settle()
        assert len(d.notes.shown) == 2
        d.backend.emit(InboxRead(sample.BOT, last(d, sample.BOT).id - 1, 1))
        await d.settle()
        assert len(d.notes.shown) == 2  # that was not the one
        d.backend.emit(InboxRead(sample.BOT, last(d, sample.BOT).id, 0))  # on the phone
        await d.settle()
        assert list(d.notes.shown) == [sample.SARA]
        d.app.app_focus = True
        await d.settle(0.3)
        await d.settle()
        assert d.notes.shown == {} and d.screen.store.chats[sample.SARA].read_inbox == message.id


async def test_deleting_takes_the_notification_away():
    async with notified() as d:
        private = d.backend.receive(sample.SARA, "oops")
        channel = d.backend.receive(sample.NEWS, "oops")
        await d.settle()
        d.backend.emit(MessagesRemoved(None, (channel.id,)))  # ids of channels are their own
        assert sample.NEWS in d.notes.shown
        d.backend.emit(MessagesRemoved(None, (private.id,)))
        d.backend.emit(MessagesRemoved(sample.NEWS, (channel.id,)))
        assert d.notes.shown == {}


async def test_many_messages_are_counted_not_piled_up(monkeypatch):
    monkeypatch.setattr(alerts, "PAUSE", 0.2)
    async with notified() as d:
        for i in range(5):
            d.backend.receive(sample.GROUP, f"message {i}", 50, "Reza")
        await d.pilot.pause()
        assert d.notes.events == [(sample.GROUP, "Peyk Developers", "Reza: message 0")]
        await d.settle(0.4)
        assert d.notes.events[1:] == [(sample.GROUP, "Peyk Developers (5)", "Reza: message 4")]
        await d.settle(0.4)
        assert len(d.notes.events) == 2
        d.backend.emit(InboxRead(sample.GROUP, last(d, sample.GROUP).id, 0))
        d.backend.receive(sample.GROUP, "again", 50, "Reza")
        await d.settle()
        assert d.notes.events[-1] == (sample.GROUP, "Peyk Developers", "Reza: again")


async def test_read_during_the_pause(monkeypatch):
    monkeypatch.setattr(alerts, "PAUSE", 0.1)
    async with notified() as d:
        d.backend.receive(sample.GROUP, "one", 50, "Reza")
        d.backend.receive(sample.GROUP, "two", 50, "Reza")
        d.backend.emit(InboxRead(sample.GROUP, last(d, sample.GROUP).id, 0))
        await d.settle(0.3)
        assert len(d.notes.events) == 1 and d.notes.shown == {}


async def test_the_same_message_twice_is_one():
    from peyk.backend.base import MessageAdded

    async with notified() as d:
        message = d.backend.receive(sample.SARA, "once")
        d.backend.emit(MessageAdded(message))
        await d.settle()
        assert len(d.notes.events) == 1 and d.notes.shown[sample.SARA][0] == "سارا محمدی"


async def test_content_can_be_hidden(monkeypatch):
    monkeypatch.setattr(alerts, "PAUSE", 0.05)
    async with notified(config=Config(show_content=False)) as d:
        d.backend.receive(sample.GROUP, "secret plan", 50, "Reza")
        await d.settle(0.2)
        assert d.notes.shown[sample.GROUP][:2] == ("Peyk Developers", "New message")
        d.backend.receive(sample.GROUP, "secret two", 50, "Reza")
        await d.settle(0.2)
        assert d.notes.shown[sample.GROUP][:2] == ("Peyk Developers", "2 new messages")
        assert "secret" not in str(d.notes.events)


async def test_spoilers_stay_covered_and_long_texts_are_cut():
    async with notified() as d:
        d.backend.receive(sample.SARA, "He is the killer", spans=(Span(10, 16, "spoiler"),))
        d.backend.receive(sample.BOT, "word " * 200)
        await d.settle()
        assert d.notes.shown[sample.SARA][1] == "He is the ▒▒▒▒▒▒"
        long = d.notes.shown[sample.BOT][1]
        assert len(long) <= 200 and long.endswith("…")


async def test_silent_messages_make_no_sound():
    async with notified() as d:
        d.backend.receive(sample.SARA, "shh", silent=True)
        await d.settle()
        assert d.notes.shown[sample.SARA][2] is True


async def test_turned_off():
    async with notified(config=Config(notifications=False)) as d:
        d.backend.receive(sample.SARA, "hello")
        await d.settle()
        assert d.notes.events == [] and d.notes.on_click is None


async def test_a_click_opens_the_chat(fronted):
    async with notified() as d:
        await d.open(sample.BOT)
        await d.press("i")
        d.backend.receive(sample.SARA, "hello")
        await d.settle()
        d.notes.on_click(sample.SARA)
        await d.settle()
        assert d.screen.active == sample.SARA and not d.screen.writing
        assert len(fronted) == 1
        d.notes.on_click(424242)  # a chat we have left meanwhile
        await d.settle()
        assert d.screen.active == sample.SARA


async def test_new_chats_are_announced_too():
    from peyk.backend.base import MessageAdded
    from peyk.models import Chat

    async with notified() as d:
        chat = Chat(777, "Stranger", Kind.USER)
        message = replace(last(d, sample.SARA), id=1, chat_id=777, text="Hi, who is this?",
                          sender_id=777, date=datetime.now().astimezone())  # fmt: skip
        d.backend.emit(MessageAdded(message, chat))
        await d.settle()
        assert d.notes.shown[777][:2] == ("Stranger", "Hi, who is this?")


async def test_nothing_is_left_behind():
    notes = Notes()
    async with notified(notes=notes) as d:
        d.backend.receive(sample.SARA, "hello")
        await d.settle()
    assert notes.events[-1] == "closed" and notes.shown == {}
    notes = Notes()
    async with notified(notes=notes) as d:
        await d.press("L", "y")
        await d.settle()
        assert notes.events == ["cleared for good"]


async def test_demo_chatter_types_first():
    backend = FakeBackend(chatter=0.1)
    heard = []
    backend.on_event(heard.append)
    async with run(backend=backend) as d:
        await d.pilot.pause(0.3)
        kinds = [type(e).__name__ for e in heard if not isinstance(e, InboxRead)]
        assert kinds[:3] == ["Typing", "MessageAdded", "ReactionsChanged"]
