"""The part of the main screen that forwards and pins messages and manages chats."""

from __future__ import annotations

import logging
import secrets
import time
from typing import TYPE_CHECKING

from textual import on

from peyk.backend.base import (
    BackendError,
    ChatFlags,
    Event,
    InboxRead,
    MessageAdded,
    MessagesPinned,
    PinsChanged,
)
from peyk.models import FOREVER, Chat, Kind, Message, State
from peyk.text import format as fmt
from peyk.ui.screens.confirm import Answer, ChoiceScreen
from peyk.ui.screens.finder import ChatFinder
from peyk.ui.widgets.pin_bar import PinBar

if TYPE_CHECKING:
    from peyk.ui.screens.main import MainScreen as _Base
else:
    from textual.screen import Screen as _Base  # a plain mixin would lose its @on handlers

log = logging.getLogger(__name__)
MUTE_FOR = (
    Answer("1", "for 1 hour", "3600"),
    Answer("8", "for 8 hours", "28800"),
    Answer("2", "for 2 days", "172800"),
    Answer("f", "until you unmute it", "forever"),
)


class Managing(_Base):
    # -- forwarding -----------------------------------------------------------

    def do_forward(self) -> None:
        message = self._selected_or_complain("forward")
        if message is None:
            return
        if message.state is not State.SENT:
            self._flash("That message is not sent yet.")
            return
        if message.service:
            self._flash("This cannot be forwarded.")
            return

        def newest_first(chat: Chat) -> tuple[bool, float]:
            return chat.kind is not Kind.SAVED, -(chat.last.date.timestamp() if chat.last else 0)

        chats = sorted((c for c in self.store.chats.values() if c.can_write), key=newest_first)

        def picked(chat: Chat | None) -> None:
            if chat:
                self.call_later(self._ask_how_to_forward, message, chat)

        finder = ChatFinder(self._backend, self._options, chats, title="Forward to")
        self.app.push_screen(finder, picked)

    def _ask_how_to_forward(self, message: Message, chat: Chat) -> None:
        self.store.chats.setdefault(chat.id, chat)
        answers = [
            Answer("y", "with the sender's name", "author"),
            Answer("h", "without it", "hidden"),
        ]

        def answered(value: str | None) -> None:
            if value:
                work = self._forward(message, chat, with_author=value == "author")
                self.run_worker(work, group="forward")

        question = ChoiceScreen(f"Forward to {chat.title}?", answers, fmt.body(message),
                                self._options)  # fmt: skip
        self.app.push_screen(question, answered)

    async def _forward(self, message: Message, chat: Chat, with_author: bool) -> None:
        # The same token if it is tried again: it may have gone through the first time.
        key = (message.chat_id, message.id, chat.id, with_author)
        token = self._forward_tokens.setdefault(key, secrets.randbits(63) or 1)
        try:
            sent = await self._backend.forward(
                message.chat_id, message.id, chat.id, token, with_author
            )
        except BackendError as error:
            self._flash(f"Not forwarded. {error.message}")
            return
        self._forward_tokens.pop(key, None)
        if sent:
            self._event(MessageAdded(sent))
        self._flash(f"Forwarded to {chat.title}.")

    # -- pinned messages ------------------------------------------------------

    def _load_pins(self, chat_id: int) -> None:
        if chat_id not in self.store.pins:
            self.run_worker(self._fetch_pins(chat_id), group=f"pins-{chat_id}", exclusive=True)

    async def _fetch_pins(self, chat_id: int) -> None:
        try:
            found = await self._backend.pinned(chat_id)
        except BackendError as error:
            log.info("pinned messages not loaded: %s", error.kind.name)
            return
        self.store.set_pins(chat_id, found)
        self._pin_index.pop(chat_id, None)
        if chat_id == self.active and not self._gone:
            self._show_pins()

    def _show_pins(self) -> None:
        bar = self.query_one(PinBar)
        pins = self.store.pins.get(self.active, []) if self.active is not None else []
        if not pins:
            bar.show(None)
            return
        index = self._pin_index.get(self.active, 0) % len(pins)  # type: ignore[arg-type]
        bar.show(pins[index], index, len(pins))

    def _pins_changed(self, changed: set[int], stale: set[int]) -> None:
        for chat_id in stale:
            self.store.pins.pop(chat_id, None)
            if chat_id == self.active:
                self._load_pins(chat_id)
        if self.active in changed:
            self._show_pins()

    def do_pinned(self) -> None:
        chat_id = self.active
        if chat_id is None:
            self._flash("Open a chat first.")
            return
        pins = self.store.pins.get(chat_id)
        if not pins:
            loading = pins is None
            self._flash("Loading pinned messages..." if loading else "No pinned messages here.")
            return
        index = self._pin_index.get(chat_id, 0) % len(pins)
        self._pin_index[chat_id] = (index + 1) % len(pins)
        self.jump(chat_id, pins[index].id)
        self._show_pins()

    @on(PinBar.Clicked)
    def _pin_bar_clicked(self) -> None:
        self.do_pinned()

    def do_pin(self) -> None:
        message = self._selected_or_complain("pin")
        chat = self._chat
        if message is None or chat is None:
            return
        if message.state is not State.SENT or message.service:
            self._flash("This cannot be pinned.")
            return

        def answered(value: str | None) -> None:
            if value:
                work = self._pin(chat.id, message.id, True, value == "silent", value == "me")
                self.run_worker(work, group="pin")

        if chat.kind is Kind.SAVED:
            answered("loud")
            return
        if chat.kind in (Kind.USER, Kind.BOT):
            answers = [Answer("y", "for both of you", "both"), Answer("m", "only for me", "me")]
        else:
            answers = [Answer("y", "and tell the members", "loud"),
                       Answer("s", "without telling them", "silent")]  # fmt: skip
        question = ChoiceScreen("Pin this message?", answers, fmt.body(message), self._options)
        self.app.push_screen(question, answered)

    def do_unpin(self) -> None:
        chat_id = self.active
        message = self.conversation.selected_message if chat_id is not None else None
        bar = self.query_one(PinBar)
        if message is None and bar.display:
            message = bar.message
        if chat_id is None or message is None:
            self._flash("Select a pinned message to unpin first.")
            return
        pins = self.store.pins.get(chat_id)
        if pins is not None and all(m.id != message.id for m in pins):
            self._flash("That message is not pinned.")
            return
        self.run_worker(self._pin(chat_id, message.id, False), group="pin")

    async def _pin(
        self, chat_id: int, message_id: int, pinned: bool, silent: bool = False,
        only_me: bool = False,
    ) -> None:
        done = "Pinned" if pinned else "Unpinned"
        try:
            await self._backend.pin(chat_id, message_id, pinned, silent, only_me)
        except BackendError as error:
            self._flash(f"Not {done.lower()}. {error.message}")
            return
        self._event(MessagesPinned(chat_id, (message_id,), pinned))
        self._flash(f"{done}.")

    # -- managing chats -------------------------------------------------------

    def do_chat_menu(self) -> None:
        chat_id = self.active if self.in_chat else self.chat_list.selected
        chat = self.store.chats.get(chat_id) if chat_id is not None else None
        if chat is None:
            self._flash("Choose a chat first.")
            return
        muted = chat.muted(time.time())
        answers = [Answer("m", "unmute", "unmute") if muted else Answer("m", "mute...", "mute")]
        if chat.kind is not Kind.SAVED:
            archive = "move out of the archive" if chat.archived else "archive"
            answers.append(Answer("a", archive, "archive"))
        answers.append(Answer("p", "unpin from the top" if chat.pin else "pin to the top", "pin"))
        if chat.has_unread:
            answers.append(Answer("r", "mark as read", "read"))
        else:
            answers.append(Answer("u", "mark as unread", "unread"))

        def answered(value: str | None) -> None:
            if value == "mute":
                self.call_later(self._ask_how_long, chat.id)
            elif value:
                self.run_worker(self._manage(chat.id, value), group="manage")

        menu = ChoiceScreen(chat.title, answers, options=self._options, stacked=True)
        self.app.push_screen(menu, answered)

    def _ask_how_long(self, chat_id: int) -> None:
        def answered(value: str | None) -> None:
            if value:
                self.run_worker(self._manage(chat_id, value), group="manage")

        chat = self.store.chats.get(chat_id)
        title = f"Mute {chat.title}" if chat else "Mute"
        self.app.push_screen(ChoiceScreen(title, list(MUTE_FOR), stacked=True), answered)

    async def _manage(self, chat_id: int, what: str) -> None:
        chat = self.store.chats.get(chat_id)
        if chat is None:
            return
        backend = self._backend
        events: list[Event] = []
        try:
            match what:
                case "unmute":
                    await backend.mute(chat_id, 0)
                    events, said = [ChatFlags(chat_id, muted_until=0.0)], "Unmuted."
                case "archive":
                    await backend.archive(chat_id, not chat.archived)
                    events = [ChatFlags(chat_id, archived=not chat.archived)]
                    said = "Moved out of the archive." if chat.archived else "Archived."
                case "pin":
                    await backend.pin_chat(chat_id, not chat.pin)
                    events = [PinsChanged()]
                    said = "Unpinned from the top." if chat.pin else "Pinned to the top."
                case "unread":
                    await backend.mark_unread(chat_id, True)
                    events, said = [ChatFlags(chat_id, marked_unread=True)], "Marked as unread."
                case "read":
                    if chat.marked_unread:
                        await backend.mark_unread(chat_id, False)
                        events.append(ChatFlags(chat_id, marked_unread=False))
                    if chat.unread and chat.last:
                        await backend.mark_read(chat_id, chat.last.id, chat.mentions > 0)
                        events.append(InboxRead(chat_id, chat.last.id))
                    said = "Marked as read."
                case _:
                    until = FOREVER if what == "forever" else time.time() + int(what)
                    await backend.mute(chat_id, until)
                    events = [ChatFlags(chat_id, muted_until=until)]
                    label = next(a.label for a in MUTE_FOR if a.value == what)
                    said = f"Muted {label}."
        except BackendError as error:
            self._flash(f"Not done. {error.message}")
            return
        for event in events:
            self._event(event)
        self._flash(said)
