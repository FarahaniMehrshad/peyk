"""The part of the main screen for reactions, typing and notifications."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from textual import on
from textual.message import Message as UiMessage

from peyk.backend.base import BackendError, Link, ReactionsChanged
from peyk.models import Chat, Kind, State
from peyk.platform import notify
from peyk.store import is_channel_id
from peyk.text import format as fmt
from peyk.ui.screens.reactions import ReactionPicker
from peyk.ui.widgets.divider import Divider

if TYPE_CHECKING:
    from peyk.ui.screens.main import MainScreen as _Base
else:
    from textual.screen import Screen as _Base  # a plain mixin would lose its @on handlers

log = logging.getLogger(__name__)
TELL_EVERY = 5.0  # seconds; telling others that we type lasts a little longer
LOOK_EVERY = 20.0  # seconds between asking for reactions, which groups do not always send
MAX_ASKED = 100


class NoticeClicked(UiMessage):
    def __init__(self, chat_id: int) -> None:
        super().__init__()
        self.chat_id = chat_id


class Extras(_Base):
    # -- reactions ----------------------------------------------------------

    def do_react(self) -> None:
        message = self._selected_or_complain("react to")
        if message is None:
            return
        if message.state is not State.SENT:
            self._flash("That message is not sent yet.")
            return
        if message.service:
            self._flash("You cannot react to this.")
            return

        def chosen(keys: tuple[str, ...] | None) -> None:
            if keys is not None:
                work = self._react(message.chat_id, message.id, keys)
                self.run_worker(work, group=f"react-{message.chat_id}-{message.id}")

        self.app.push_screen(
            ReactionPicker(self._backend, self._options, message, self._keys), chosen
        )

    async def _react(self, chat_id: int, message_id: int, keys: tuple[str, ...]) -> None:
        try:
            reactions = await self._backend.react(chat_id, message_id, keys)
        except BackendError as error:
            self._flash(f"No reaction given. {error.message}")
            return
        self._event(ReactionsChanged(chat_id, message_id, reactions))

    def _look_for_reactions(self) -> None:
        """Only for what is in front of the user."""
        chat_id = self.active
        if chat_id is None or not is_channel_id(chat_id) or not self._in_view(chat_id):
            return
        ids = self.conversation.visible_ids()[-MAX_ASKED:]
        if ids and self._backend.link is Link.ONLINE:
            work = self._refresh_reactions(chat_id, ids)
            self.run_worker(work, group="reactions", exclusive=True)

    async def _refresh_reactions(self, chat_id: int, ids: list[int]) -> None:
        try:
            await self._backend.refresh_reactions(chat_id, ids)
        except BackendError as error:
            log.info("reactions not refreshed: %s", error.kind.name)

    # -- typing -------------------------------------------------------------

    def _tell_typing(self) -> None:
        chat, draft = self._chat, self.draft
        if (
            not self._show_typing_to_others
            or chat is None
            or chat.kind in (Kind.SAVED, Kind.CHANNEL)
            or draft.edit_id
            or not draft.text.strip()
            or self._backend.link is not Link.ONLINE
        ):
            return
        now = time.monotonic()
        if now - self._told_typing.get(chat.id, -TELL_EVERY) >= TELL_EVERY:
            self._told_typing[chat.id] = now
            self.run_worker(self._send_typing(chat.id), group="typing")

    async def _send_typing(self, chat_id: int) -> None:
        try:
            await self._backend.typing(chat_id)
        except BackendError as error:
            log.info("typing not told: %s", error.kind.name)

    def _show_typing(self) -> None:
        doing = {
            chat.id: pieces
            for chat in self.store.chats.values()
            if (pieces := self._doing(chat, short=True))
        }
        self.chat_list.show_doing(doing)
        chat = self._chat
        self.query_one(Divider).show(self._doing(chat) if chat else [])
        if self.store.anyone_typing and self._typing_timer is None:
            self._typing_timer = self.set_timer(1, self._typing_passed)

    def _doing(self, chat: Chat, short: bool = False) -> list[str]:
        typists = self.store.typists(chat.id)
        if not typists:
            return []
        alone = chat.kind in (Kind.USER, Kind.BOT)
        if short and alone:
            return [f"{typists[0].what}…"]
        names = [(t.name.split()[0] if short and t.name else t.name, t.what) for t in typists]
        return fmt.typing(names, chat.title if alone else "")

    def _typing_passed(self) -> None:
        self._typing_timer = None
        self.store.forget_old_typing()
        if not self._gone:
            self._show_typing()

    # -- notifications ------------------------------------------------------

    def _in_view(self, chat_id: int) -> bool:
        return (
            self.app.app_focus
            and chat_id == self.active
            and not self._gone
            and bool(self.query_one("#conversation").display)
        )

    def _focus_changed(self, focused: bool) -> None:
        self._read_soon()
        if focused and self._alerts:
            self._window = notify.terminal_window() or self._window

    def _notice_clicked_somewhere(self, chat_id: int) -> None:
        """From a thread of Windows."""
        self.post_message(NoticeClicked(chat_id))

    @on(NoticeClicked)
    def _notice_clicked(self, event: NoticeClicked) -> None:
        notify.bring_to_front(self._window)
        if event.chat_id in self.store.chats:
            self.set_focus(None)  # do not write into another chat by accident
            self.open_chat(event.chat_id)
