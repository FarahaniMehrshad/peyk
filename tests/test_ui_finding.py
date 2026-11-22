"""The finders, jumping, commands and help."""

import pytest

from driver import OFFLINE, run
from peyk.backend import sample
from peyk.backend.base import BackendError, ErrorKind, MessagesRemoved
from peyk.backend.fake import FakeBackend
from peyk.ui.keymap import DEFAULT_KEYS
from peyk.ui.screens import finder as finder_module
from peyk.ui.screens.command import COMMANDS, find
from peyk.ui.screens.finder import ChatFinder, MessageFinder, SenderFinder
from peyk.ui.screens.help import HelpScreen, help_text
from peyk.ui.screens.main import MainScreen
from peyk.ui.widgets.edit_box import EditBox
from peyk.ui.widgets.results import ResultList


@pytest.fixture(autouse=True)
def quick_typing(monkeypatch):
    monkeypatch.setattr(finder_module, "TYPING_PAUSE", 0.02)


async def search(d, text):
    d.app.screen.query_one(EditBox).insert(text)
    await d.settle(0.1)
    await d.settle()


def titles(d):
    return [e.title for e in d.app.screen.query_one(ResultList).entries]


def note(d):
    return d.app.screen.query_one(ResultList).note


# -- chats


async def test_find_chat():
    async with run() as d:
        await d.press("/")
        assert isinstance(d.app.screen, ChatFinder)
        assert len(titles(d)) == len(d.backend.chat_map)
        await search(d, "dev")
        assert titles(d)[0] == "Peyk Developers"
        await d.press("enter")
        assert d.screen.active == sample.GROUP and d.screen.in_chat


async def test_find_chat_in_persian_spelled_differently():
    async with run() as d:
        await d.press("/")
        await search(d, "محمدي")
        assert titles(d)[0] == "سارا محمدی"


async def test_archived_chats_and_contacts_are_found():
    async with run() as d:
        await d.press("/")
        await search(d, "old fr")
        assert titles(d) == ["Old Friend"]
        await d.press("escape", "/")
        await search(d, "zahra")
        assert titles(d) == ["Zahra Contact"]
        await d.press("enter")
        assert d.screen.active == 60 and "Zahra Contact" in d.text()
        await d.press("i", "h", "i", "enter")
        assert d.backend.messages[60][-1].text == "hi"
        assert d.screen.store.chats[60].last.text == "hi"


async def test_moving_in_results():
    async with run() as d:
        await d.press("/", "down", "down", "up", "ctrl+n", "ctrl+j", "ctrl+k", "ctrl+p")
        assert d.app.screen.results.index == 1
        await d.press("pagedown", "pagedown", "pagedown")
        assert d.app.screen.results.index == len(titles(d)) - 1
        await d.press("pageup", "pageup", "pageup", "up")
        assert d.app.screen.results.index == 0


async def test_nothing_found_and_closing():
    async with run() as d:
        await d.press("/")
        await search(d, "qqqq")
        assert titles(d) == [] and note(d) == "Nothing found."
        await d.press("enter")
        assert isinstance(d.app.screen, ChatFinder)
        await d.press("escape")
        assert isinstance(d.app.screen, MainScreen) and d.screen.active is None


async def test_server_trouble_keeps_local_results():
    async with run() as d:
        d.backend.fail("find_chats", OFFLINE)
        await d.press("/")
        await search(d, "dev")
        assert titles(d) == ["Peyk Developers"] and "Could not reach" in note(d)


async def test_click_a_result():
    async with run() as d:
        await d.press("/")
        await d.pilot.click("#results", offset=(3, 1))
        await d.settle()
        assert isinstance(d.app.screen, MainScreen) and d.screen.active is not None


async def test_names_are_not_markup():
    backend = FakeBackend()
    from dataclasses import replace

    chat = backend.chat_map[sample.GROUP]
    backend.chat_map[sample.GROUP] = replace(chat, title="[bold]x[/] [oops")
    async with run(backend=backend) as d:
        await d.open(sample.GROUP)
        await d.press("?")
        assert "[bold]x[/] [oops" in d.text()


# -- messages


async def test_search_in_this_chat_then_everywhere():
    async with run() as d:
        await d.open(sample.GROUP)
        await d.press("?")
        assert isinstance(d.app.screen, MessageFinder) and "two letters" in note(d)
        await search(d, "message")
        assert titles(d) == [] and note(d) == "Nothing found."
        await d.press("tab")
        await d.settle(0.1)
        await d.settle()
        assert len(titles(d)) == 30
        assert ("search", "message:None:None") in d.backend.calls


async def test_more_results_load_at_the_end():
    async with run() as d:
        await d.press("?")
        await search(d, "filler")
        assert len(titles(d)) == 30
        for _ in range(4):
            await d.press("pagedown")
        assert len(titles(d)) == 60


async def test_search_from_the_list_covers_all_chats():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("h", "?")
        await search(d, "milk")
        assert titles(d) == ["Saved Messages"]


async def test_failed_search_is_reported():
    async with run() as d:
        d.backend.fail("search", BackendError(ErrorKind.FLOOD, "Wait a bit."))
        await d.press("?")
        await search(d, "milk")
        assert note(d) == "Wait a bit."


async def test_jump_to_a_loaded_message():
    async with run() as d:
        await d.open(sample.GROUP)
        await d.press("?")
        await search(d, "build is green")
        await d.press("enter")
        assert d.talk.selected_message.text.startswith("Hey everyone")
        assert not any(name == "around" for name, _ in d.backend.calls)
        assert any("Hey everyone" in line for line in d.said())


async def jump_far_back(d):
    await d.open(sample.LONG)
    await d.press("?")
    await search(d, "Message 13.")
    await d.press("enter")
    await d.settle()


async def test_jump_far_back_and_return():
    async with run() as d:
        await jump_far_back(d)
        assert d.talk.selected_message.text.startswith("Message 13.")
        assert not d.talk.history.at_end and "Older messages" in d.text()
        ids = [m.id for m in d.talk.history.messages]
        assert ids == list(range(ids[0], ids[-1] + 1))
        await d.press("G")
        await d.settle()
        assert d.talk.history.at_end and d.talk.lines_up == 0
        assert d.talk.history.messages[-1].id == d.backend.messages[sample.LONG][-1].id
        assert "Older messages" not in d.text()


async def test_scrolling_down_from_an_old_message_reaches_the_newest():
    async with run() as d:
        await jump_far_back(d)
        for _ in range(250):
            if d.talk.history.at_end and d.talk.lines_up == 0:
                break
            await d.press("J")
        history = d.talk.history
        assert history.at_end
        ids = [m.id for m in history.messages]
        assert ids == list(range(ids[0], ids[-1] + 1))
        assert ids[-1] == d.backend.messages[sample.LONG][-1].id


async def test_loading_newer_keeps_the_view():
    async with run() as d:
        await jump_far_back(d)
        d.hold_reading()
        for _ in range(60):
            if any(n == "history" and "after" in a for n, a in d.backend.calls):
                break
            await d.pilot.press("J")
            await d.pilot.pause()
            moved = d.said()[0]
            await d.settle()
            assert d.said()[0] == moved
        else:
            pytest.fail("never asked for newer messages")


async def test_new_messages_do_not_land_in_old_ones():
    async with run() as d:
        await jump_far_back(d)
        seen = d.said()
        d.backend.receive(sample.LONG, "brand new")
        await d.settle()
        assert d.said() == seen
        assert d.screen.store.chats[sample.LONG].last.text == "brand new"
        await d.press("G")
        await d.settle()
        assert "brand new" in "\n".join(d.said())


async def test_writing_from_old_messages_returns_to_the_newest():
    async with run() as d:
        await jump_far_back(d)
        await d.press("i", "h", "i", "enter")
        await d.settle()
        assert d.talk.history.at_end
        assert sum(line.split()[:1] == ["hi"] for line in d.said()) == 1


async def test_leaving_old_messages_and_coming_back_shows_the_newest():
    async with run() as d:
        await jump_far_back(d)
        await d.open(sample.SARA)
        await d.open(sample.LONG)
        assert d.talk.history.at_end and d.talk.lines_up == 0


async def test_reconnecting_does_not_lose_the_place():
    from peyk.backend.base import Link

    async with run() as d:
        await jump_far_back(d)
        selected = d.talk.selected
        d.backend.set_link(Link.OFFLINE)
        d.backend.set_link(Link.ONLINE)
        await d.settle()
        await d.settle()
        assert d.talk.selected == selected and not d.talk.history.at_end


async def test_jump_into_another_chat():
    async with run() as d:
        await d.press("?")
        await search(d, "Channel-style group message 5")
        await d.press("enter")
        await d.settle()
        assert d.screen.active == sample.BIG
        assert d.talk.selected_message.text.startswith("Channel-style group message 5")


async def test_jump_to_a_message_deleted_meanwhile():
    async with run() as d:
        await d.open(sample.LONG)
        await d.press("?")
        await search(d, "Message 13.")
        target = d.app.screen.results.chosen.value.message.id
        d.backend.messages[sample.LONG] = [
            m for m in d.backend.messages[sample.LONG] if m.id != target
        ]
        d.backend.emit(MessagesRemoved(sample.LONG, (target,)))
        await d.press("enter")
        await d.settle()
        assert "no longer exists" in d.text() and d.talk.history.messages


async def test_failed_jump_still_opens_the_chat():
    async with run() as d:
        d.backend.fail("around", OFFLINE)
        await d.press("?")
        await search(d, "Channel-style group message 5")
        await d.press("enter")
        await d.settle()
        assert d.screen.active == sample.BIG and d.talk.history.messages


# -- senders


async def test_find_sender_and_their_messages():
    async with run() as d:
        await d.open(sample.GROUP)
        await d.press("@")
        assert isinstance(d.app.screen, SenderFinder)
        assert {"Reza", "Mina K.", "علی", "You"} <= set(titles(d))
        await search(d, "mina")
        assert titles(d) == ["Mina K."]
        await d.press("enter")
        await d.settle(0.1)
        await d.settle()
        assert isinstance(d.app.screen, MessageFinder)
        assert set(titles(d)) == {"Mina K."}
        await search(d, "sure")
        assert len(titles(d)) == 1
        await d.press("enter")
        assert d.talk.selected_message.text == "Sure"


async def test_senders_of_a_private_chat():
    async with run() as d:
        await d.open(sample.SARA)
        await d.press("@")
        assert titles(d) == ["سارا محمدی", "You"]
        assert not any(name == "members" for name, _ in d.backend.calls)


async def test_senders_need_a_chat_that_has_them():
    async with run() as d:
        await d.press("@")
        assert isinstance(d.app.screen, MainScreen)
        await d.open(sample.NEWS)
        await d.press("@")
        assert isinstance(d.app.screen, MainScreen)


# -- commands and help


async def test_commands():
    async with run() as d:
        await d.press(":")
        await d.press(*"nope", "enter")
        assert "No command 'nope'" in d.text()
        await d.press("ctrl+u", *"sav", "tab", "enter")
        await d.settle()
        assert d.screen.active == sample.ME
        await d.press(":", "h", "enter")
        await d.settle()
        assert isinstance(d.app.screen, HelpScreen)
        await d.press("j", "J", "G", "k", "q")
        assert isinstance(d.app.screen, MainScreen)
        await d.press(":", "escape")
        assert isinstance(d.app.screen, MainScreen)
        await d.press(":", "enter")
        assert isinstance(d.app.screen, MainScreen)


async def test_read_command():
    async with run() as d:
        d.hold_reading()
        await d.press("j", "j", ":", *"read", "enter")
        await d.settle()
        assert d.screen.store.chats[sample.NEWS].unread == 0


async def test_quit_command():
    async with run() as d:
        await d.press(":", "q", "enter")
        await d.settle()
        assert not d.app.is_running or d.app._exit


def test_every_command_exists():
    for command in COMMANDS:
        assert hasattr(MainScreen, f"do_{command.action}")
    assert find("q").name == "quit" and find(":HELP").name == "help"
    assert find("sa").name == "saved" and find("") is None and find("zzz") is None


def test_help_covers_every_key():
    text = help_text(DEFAULT_KEYS).plain
    assert "{" not in text.replace("{}", "")
    for action, key in DEFAULT_KEYS.items():
        assert hasattr(MainScreen, f"do_{action}")
        if len(key) == 1:
            assert key in text
