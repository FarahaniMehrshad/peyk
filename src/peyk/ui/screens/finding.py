"""The part of the main screen that finds things and jumps to them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from peyk.backend.base import BackendError
from peyk.models import Chat, Found, Kind, User
from peyk.ui.screens.command import CommandScreen
from peyk.ui.screens.finder import ChatFinder, MessageFinder, SenderFinder, senders
from peyk.ui.screens.help import HelpScreen

if TYPE_CHECKING:
    from peyk.ui.screens.main import MainScreen as _Base
else:
    from textual.screen import Screen as _Base  # a plain mixin would lose its @on handlers

WINDOW = 60  # messages loaded around one we jump to


class Finding(_Base):
    def do_find_chat(self) -> None:
        chats = sorted(
            self.store.chats.values(),
            key=lambda c: c.last.date.timestamp() if c.last else 0.0,
            reverse=True,
        )
        self.app.push_screen(
            ChatFinder(self._backend, self._options, chats), self._chat_found
        )

    def _chat_found(self, chat: Chat | None) -> None:
        if chat:
            self.store.chats.setdefault(chat.id, chat)
            self.open_chat(chat.id)

    def do_find_message(self) -> None:
        finder = MessageFinder(
            self._backend, self._options, self._chat, everywhere=not self.in_chat
        )
        self.app.push_screen(finder, self._message_found)

    def _message_found(self, found: Found | None) -> None:
        if found is None:
            return
        message = found.message
        if found.chat:
            self.store.chats.setdefault(message.chat_id, found.chat)
        self.jump(message.chat_id, message.id)

    def do_find_sender(self) -> None:
        chat = self._chat
        if chat is None:
            self._flash("Open a chat first.")
            return
        if chat.kind is Kind.CHANNEL:
            self._flash("Channels have no senders to choose from.")
            return
        known = senders(self.store.history(chat.id).messages, self._me)
        if chat.kind in (Kind.USER, Kind.BOT) and chat.id not in {u.id for u in known}:
            known.insert(0, User(chat.id, chat.title, chat.username))

        def picked(user: User | None) -> None:
            if user:
                finder = MessageFinder(self._backend, self._options, chat, sender=user)
                self.app.push_screen(finder, self._message_found)

        self.app.push_screen(SenderFinder(self._backend, self._options, chat, known), picked)

    # -- jumping ------------------------------------------------------------

    def do_replied_to(self) -> None:
        """Go to the message the selected one answers."""
        message = self._selected_or_complain("go to what it replies to")
        if message is None:
            return
        if message.reply is None:
            self._flash("This message is not a reply.")
            return
        self.jump(message.chat_id, message.reply.id)

    def jump(self, chat_id: int, message_id: int) -> None:
        """Open a chat at one of its messages."""
        history = self.store.history(chat_id)
        if history.loaded and history.get(message_id):
            self.open_chat(chat_id)
            self.conversation.show_message(message_id)
            return
        self.open_chat(chat_id, load=False)
        self.conversation.set_note("Loading messages...")
        work = self._load_around(chat_id, message_id)
        self.run_worker(work, group=f"open-{chat_id}", exclusive=True)

    async def _load_around(self, chat_id: int, message_id: int) -> None:
        history = self.store.history(chat_id)
        try:
            window = await self._backend.around(chat_id, message_id, WINDOW)
        except BackendError as error:
            if chat_id == self.active:
                self.conversation.set_note(error.message)
                if not history.loaded:
                    await self._open_history(chat_id)
            return
        chat = self.store.chats.get(chat_id)
        if chat is None or chat_id != self.active or self._gone:
            return
        if not window:
            await self._open_history(chat_id)
            return
        history.jump(window)
        history.at_end = chat.last is None or window[-1].id >= chat.last.id
        self._open_conversation(chat)
        if not self.conversation.show_message(message_id):
            self._flash("That message no longer exists.")

    def _note_place(self) -> None:
        """Say so when the newest messages are further down."""
        if self.active is not None:
            history = self.store.history(self.active)
            away = history.loaded and not history.at_end
            key = self._keys["newest"]
            note = f"Older messages. {key} goes to the newest." if away else ""
            self.conversation.set_note(note)

    async def _load_newer(self, chat_id: int) -> None:
        history = self.store.history(chat_id)
        try:
            if history.messages and not history.at_end:
                after = history.messages[-1].id
                newer = await self._backend.history(chat_id, after=after, limit=self.PAGE)
                if history.messages and history.messages[-1].id == after and not history.at_end:
                    chat = self.store.chats.get(chat_id)
                    last = newer[-1].id if newer else after
                    # A message may have come in while this page was on its way.
                    caught_up = not newer or not (chat and chat.last and chat.last.id > last)
                    history.add_newer(newer, at_end=len(newer) < self.PAGE and caught_up)
                    if chat_id == self.active:
                        self.conversation.newer_loaded()
                        self._note_place()
        except BackendError as error:
            if chat_id == self.active:
                self._flash(error.message)
        finally:
            self._loading_newer.discard(chat_id)

    # -- commands and help --------------------------------------------------

    def do_command(self) -> None:
        self.app.push_screen(CommandScreen(self._options), self._command_given)

    def _command_given(self, action: str | None) -> None:
        if action:
            action, _, argument = action.partition(" ")
            work = getattr(self, f"do_{action}")
            # After the command bar is gone: the command may open another screen.
            self.call_later(work, argument) if argument else self.call_later(work)

    def do_help(self) -> None:
        self.app.push_screen(HelpScreen(self._keys))

    def do_saved(self) -> None:
        saved = next((c for c in self.store.chats.values() if c.kind is Kind.SAVED), None)
        if saved is None and self._me:
            saved = Chat(self._me.id, "Saved Messages", Kind.SAVED)
        self._chat_found(saved)

    def do_mark_read(self) -> None:
        chat_id = self.active if self.in_chat else self.chat_list.selected
        chat = self.store.chats.get(chat_id) if chat_id is not None else None
        if chat and chat.last:
            work = self._mark_read(chat.id, chat.last.id, chat.mentions > 0)
            self.run_worker(work, group="read")
