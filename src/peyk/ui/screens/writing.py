"""The part of the main screen that writes: sending, replying, editing, deleting."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual import on

from peyk.backend.base import BackendError, MessageAdded, MessageChanged, MessagesRemoved
from peyk.keys import hints, pretty
from peyk.models import Attachment, Chat, Kind, Message, Reply, State
from peyk.outbox import FIRST_ID
from peyk.platform import clipboard
from peyk.store import is_channel_id
from peyk.text import format as fmt
from peyk.text import markup
from peyk.text.sanitize import sanitize_line
from peyk.ui.screens.confirm import Answer, ChoiceScreen
from peyk.ui.widgets.divider import Divider
from peyk.ui.widgets.edit_box import EditBox

if TYPE_CHECKING:
    from peyk.ui.screens.main import MainScreen as _Base
else:
    from textual.screen import Screen as _Base  # a plain mixin would lose its @on handlers

log = logging.getLogger(__name__)
MAX_LENGTH = 4096
MAX_CAPTION = 1024
_CAPTIONED = frozenset({"photo", "video", "gif", "voice", "audio", "document"})


@dataclass(slots=True)
class Draft:
    """What is being written in one chat."""

    text: str = ""
    reply: Reply | None = None
    attachment: Attachment | None = None
    edit_id: int = 0
    stash: tuple[str, Reply | None] = ("", None)  # put aside while editing
    saving: bool = False

    @property
    def unsent(self) -> bool:
        return bool(
            self.text.strip() or self.attachment or (self.edit_id and self.stash[0].strip())
        )

    @property
    def max_length(self) -> int:
        return MAX_CAPTION if self.attachment and not self.edit_id else MAX_LENGTH

    def stop_editing(self) -> None:
        self.text, self.reply = self.stash
        self.edit_id, self.stash, self.saving = 0, ("", None), False


def editable(message: Message) -> bool:
    """Telegram also refuses when it is too old."""
    return (
        message.outgoing
        and not message.service
        and not message.forwarded
        and message.state is State.SENT
        and (message.media is None or message.media.kind in _CAPTIONED)
    )


def deletions(message: Message, chat: Chat) -> list[Answer]:
    """Ways to delete a message, depending on the chat and whose it is."""
    everyone = Answer("y", "delete for everyone", "everyone")
    only_me = Answer("m", "delete only for me", "me")
    if chat.kind is Kind.SAVED:
        return [Answer("y", "delete", "everyone")]
    if chat.kind in (Kind.USER, Kind.BOT):
        return [everyone, only_me]
    mine = message.outgoing or chat.can_delete_any
    if is_channel_id(chat.id):
        return [everyone] if mine else []
    return [everyone, only_me] if mine else [only_me]


def _described(attachment: Attachment) -> str:
    how = "Photo" if attachment.as_photo else "File"
    return f"{how} {sanitize_line(attachment.name)} ({fmt.size(attachment.size)})"


class Writing(_Base):
    @property
    def box(self) -> EditBox:
        return self.query_one("#compose", EditBox)

    @property
    def writing(self) -> bool:
        return not self._gone and self.box.has_focus

    @property
    def draft(self) -> Draft:
        assert self.active is not None
        return self._drafts.setdefault(self.active, Draft())

    @property
    def _chat(self) -> Chat | None:
        return self.store.chats.get(self.active) if self.active is not None else None

    # -- the compose box ----------------------------------------------------

    def _show_draft(self) -> None:
        """Make the box match the open chat."""
        box, chat = self.box, self._chat
        box.display = self.query_one(Divider).display = chat is not None
        self._show_typing()
        if chat is None:
            return
        box.can_focus = chat.can_write
        if not chat.can_write and box.has_focus:
            self.set_focus(None)
        box.frozen = self.draft.saving
        if box.text != self.draft.text:
            box.set(self.draft.text)
        self._show_header()

    def _show_header(self) -> None:
        box, draft = self.box, self.draft
        header = ""
        if draft.saving:
            header = "Saving..."
        elif draft.edit_id:
            header = "Edit"
        else:
            parts = []
            if draft.attachment:
                parts.append(_described(draft.attachment))
            if draft.reply:
                who = f" to {draft.reply.name}" if draft.reply.name else ""
                parts.append(f"Reply{who}: {draft.reply.text}")
            header = "  ".join(parts)
        length = markup.utf16_length(draft.text)
        if length > draft.max_length * 0.9:
            header = f"{length}/{draft.max_length}  {header}"
        box.set_header(header)
        chat = self._chat
        if chat and not chat.can_write:
            box.set_placeholder("You cannot write in this chat.")
        else:
            key = pretty(self._keys["write"])
            invitation = "Write a caption, or just press Enter" if draft.attachment else (
                "Write a message"
            )
            waiting = f"{key}: write a message" if key else "Write a message"
            box.set_placeholder(invitation if box.has_focus else waiting)

    @on(EditBox.Changed, "#compose")
    def _draft_changed(self) -> None:
        if self.active is not None:
            self.draft.text = self.box.text
            self._show_header()
            self._tell_typing()

    @on(EditBox.Left, "#compose")
    def _stop_writing(self) -> None:
        self.set_focus(None)

    @on(EditBox.Clicked, "#compose")
    def _box_clicked(self) -> None:
        self.do_write()

    def do_write(self) -> None:
        if not self.in_chat and self.chat_list.selected not in (None, self.active):
            self.open_chat(self.chat_list.selected)
        chat = self._chat
        if chat is None:
            self._flash("Open a chat first.")
        elif not chat.can_write:
            self._flash("You cannot write in this chat.")
        else:
            self.in_chat = True
            self._arrange()
            self.box.focus()

    # -- sending ------------------------------------------------------------

    @on(EditBox.Submitted, "#compose")
    def _submitted(self) -> None:
        if self.active is None:
            return
        draft = self.draft
        if draft.edit_id:
            self._start_saving()
            return
        marks = None if self._formatting else [False] * len(draft.text)
        parsed = markup.parse(draft.text, marks)
        if not parsed.text and not draft.attachment:
            return
        length = markup.utf16_length(parsed.text)
        if length > draft.max_length:
            self._flash(f"Too long by {length - draft.max_length} characters.")
            return
        me = self._me.id if self._me else 0
        typed, reply, attachment = draft.text, draft.reply, draft.attachment
        draft.text, draft.reply, draft.attachment = "", None, None
        self._show_draft()
        self._told_typing.pop(self.active, None)
        self.outbox.add(self.active, parsed.text, parsed.spans, reply, me, typed, attachment)
        self.do_newest()

    def _outbox_changed(self, chat_id: int) -> None:
        for key in [k for k in self.progress if k[0] == chat_id and k[1] >= FIRST_ID]:
            if self.outbox.get(*key) is None:
                del self.progress[key]
        if chat_id == self.active and not self._gone:
            self.conversation.pending = self.outbox.waiting(chat_id)
            self.conversation.content_changed()

    def _was_sent(self, chat_id: int, message: Message | None) -> None:
        if self._gone:
            return
        if chat_id == self.active:
            self.conversation.pending = self.outbox.waiting(chat_id)
        if message:
            self._event(MessageAdded(message))
        elif chat_id == self.active:
            # An earlier attempt had arrived after all: fetch it.
            self.run_worker(self._open_history(chat_id), group=f"open-{chat_id}", exclusive=True)
        else:
            self.store.history(chat_id).loaded = False
        self._outbox_changed(chat_id)

    # -- replying and editing -----------------------------------------------

    def do_reply(self) -> None:
        message = self._selected_or_complain("reply to")
        if message is None or not self._can_write_here():
            return
        if message.state is not State.SENT:
            self._flash("That message is not sent yet.")
            return
        draft = self.draft
        if draft.edit_id:
            draft.stop_editing()
        name = "You" if message.outgoing else message.sender
        draft.reply = Reply(message.id, name, fmt.body(message))
        self.conversation.deselect()
        self._show_draft()
        self.do_write()

    def do_edit(self) -> None:
        if self.active is None or not self._can_write_here():
            return
        message = self.conversation.selected_message
        if message and message.state is not State.SENT:
            self._take_back(message)
            return
        if message is None:
            mine = [m for m in self.store.history(self.active).messages if editable(m)]
            message = mine[-1] if mine else None
        if message is None:
            self._flash("You have no message here to edit.")
            return
        if not editable(message):
            mine = message.outgoing and not message.service
            self._flash(
                "This kind of message cannot be edited."
                if mine
                else "You can only edit your own messages."
            )
            return
        draft = self.draft
        if draft.saving:
            return
        if draft.edit_id != message.id:
            if not draft.edit_id:
                draft.stash = (draft.text, draft.reply)
            draft.edit_id, draft.text, draft.reply = message.id, message.text, None
        self.conversation.deselect()
        self._show_draft()
        self.do_write()

    def _take_back(self, message: Message) -> None:
        """An unsent message returns to the box."""
        if self.draft.unsent:
            self._flash("Send or clear what you are writing first.")
            return
        taken = self.outbox.take_back(message.chat_id, message.id)
        if taken is None:
            self._flash("It is being sent right now.")
            return
        self.draft.text, self.draft.reply, self.draft.attachment = taken
        self._show_draft()
        self.do_write()

    def _start_saving(self) -> None:
        draft = self.draft
        if draft.saving or self.active is None:
            return
        message = self.store.history(self.active).get(draft.edit_id)
        if not draft.text.strip() and not (message and message.media):
            self._flash("An empty message cannot be saved. Delete it instead.")
            return
        if markup.utf16_length(draft.text) > MAX_LENGTH:
            self._flash("Too long.")
            return
        draft.saving = True
        self._show_draft()
        self.run_worker(self._save(self.active, draft), group="edit")

    async def _save(self, chat_id: int, draft: Draft) -> None:
        try:
            message = await self._backend.edit(
                chat_id, draft.edit_id, draft.text, self._formatting
            )
        except BackendError as error:
            draft.saving = False
            self._flash(f"Not saved. {error.message}")
        else:
            draft.stop_editing()
            self._event(MessageChanged(message))
        if chat_id == self.active and not self._gone:
            self._show_draft()

    def _cancel_extras(self) -> bool:
        """Drop the edit, the file or the reply. True if there was one."""
        if self.active is None:
            return False
        draft = self.draft
        if draft.saving or not (draft.edit_id or draft.attachment or draft.reply):
            return False
        if draft.edit_id:
            draft.stop_editing()
        elif draft.attachment:
            draft.attachment = None
        else:
            draft.reply = None
        self._show_draft()
        return True

    def _drafts_lost(self, event: MessagesRemoved) -> None:
        """Messages we were replying to or editing are gone."""
        for chat_id, draft in self._drafts.items():
            if event.chat_id not in (None, chat_id):
                continue
            if event.chat_id is None and is_channel_id(chat_id):
                continue
            if draft.reply and draft.reply.id in event.ids:
                draft.reply = None
            if draft.edit_id in event.ids:
                draft.edit_id, draft.saving = 0, False  # what was typed stays
            if chat_id == self.active:
                self._show_draft()

    # -- deleting and copying -----------------------------------------------

    def do_delete(self) -> None:
        message = self._selected_or_complain("delete")
        chat = self._chat
        if message is None or chat is None:
            return
        if message.state is not State.SENT:
            if self.outbox.discard(chat.id, message.id) is None:
                self._flash("It is being sent right now.")
            return
        answers = deletions(message, chat)
        if not answers:
            self._flash("You cannot delete other people's messages here.")
            return
        quote = "" if message.service else fmt.body(message)

        def answered(value: str | None) -> None:
            if value:
                work = self._delete(chat.id, message.id, value == "everyone")
                self.run_worker(work, group="delete")

        self.app.push_screen(
            ChoiceScreen("Delete this message?", answers, quote, self._options), answered
        )

    async def _delete(self, chat_id: int, message_id: int, for_everyone: bool) -> None:
        try:
            await self._backend.delete(chat_id, [message_id], for_everyone)
        except BackendError as error:
            self._flash(f"Not deleted. {error.message}")
            return
        self._event(MessagesRemoved(chat_id, (message_id,)))

    def do_copy(self) -> None:
        message = self._selected_or_complain("copy")
        if message:
            text = message.text or fmt.body(message)
            copied = clipboard.copy(text)
            self._flash("Copied." if copied else "The clipboard is in use by another program.")

    # -- helpers

    def _selected_or_complain(self, what: str) -> Message | None:
        message = self.conversation.selected_message if self.active is not None else None
        if message is None:
            how = hints(self._keys, ("down up", "select"))
            self._flash(f"Select a message to {what} first" + (f": {how}." if how else "."))
        return message

    def _can_write_here(self) -> bool:
        chat = self._chat
        if chat and not chat.can_write:
            self._flash("You cannot write in this chat.")
            return False
        return chat is not None
