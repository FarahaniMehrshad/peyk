"""Everything known about chats and messages. In memory only."""

from __future__ import annotations

import time
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from peyk.backend.base import (
    ChatFlags,
    Event,
    FoldersChanged,
    InboxRead,
    MessageAdded,
    MessageChanged,
    MessagesRemoved,
    OutboxRead,
    PinsChanged,
)
from peyk.models import Chat, Folder, Message

ALL = 0
ARCHIVE = -1
_FIRST_CHANNEL = -1_000_000_000_000  # ids at or below this are channels and supergroups


def is_channel_id(chat_id: int) -> bool:
    return chat_id <= _FIRST_CHANNEL


class History:
    """A gapless run of one chat's messages. Ends at the newest, unless we jumped back."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.complete = False  # nothing older exists
        self.at_end = True  # nothing newer exists
        self.loaded = False

    def _find(self, message_id: int) -> int | None:
        i = bisect_left(self.messages, message_id, key=lambda m: m.id)
        found = i < len(self.messages) and self.messages[i].id == message_id
        return i if found else None

    def get(self, message_id: int) -> Message | None:
        i = self._find(message_id)
        return None if i is None else self.messages[i]

    def put(self, message: Message) -> bool:
        """True if it was not there before."""
        i = bisect_left(self.messages, message.id, key=lambda m: m.id)
        if i < len(self.messages) and self.messages[i].id == message.id:
            self.messages[i] = message
            return False
        self.messages.insert(i, message)
        return True

    def remove(self, message_id: int) -> Message | None:
        i = self._find(message_id)
        return None if i is None else self.messages.pop(i)

    def add_older(self, older: list[Message], complete: bool) -> None:
        for message in older:
            self.put(message)
        self.complete = complete

    def add_newer(self, newer: list[Message], at_end: bool) -> None:
        for message in newer:
            self.put(message)
        self.at_end = at_end

    def jump(self, window: list[Message]) -> None:
        """Show another part of the chat instead."""
        self.messages = list(window)
        self.complete = self.at_end = False
        self.loaded = True

    def refresh(self, newest: list[Message], complete: bool) -> None:
        """Take a fresh newest page, dropping what we have if there is a gap between."""
        self.at_end = True
        if not newest:
            self.messages, self.complete, self.loaded = [], True, True
            return
        first = newest[0].id
        overlaps = bool(self.messages) and self.messages[-1].id >= first
        if overlaps:
            # Messages inside the page that it lacks were deleted meanwhile.
            kept = [m for m in self.messages if m.id < first]
            self.messages = kept + newest
            self.complete = self.complete or complete
        else:
            self.messages = list(newest)
            self.complete = complete
        self.loaded = True


@dataclass(slots=True)
class Change:
    """What the UI must redraw or load after an event."""

    chats: bool = False
    messages: set[int] = field(default_factory=set)  # chats whose history changed
    stale: set[int] = field(default_factory=set)  # chats whose last message is unknown
    reload_chats: bool = False
    reload_folders: bool = False


class Store:
    def __init__(self) -> None:
        self.chats: dict[int, Chat] = {}
        self.folders: list[Folder] = []
        self.histories: dict[int, History] = {}

    # -- reading

    def history(self, chat_id: int) -> History:
        return self.histories.setdefault(chat_id, History())

    def visible(self, folder_id: int, now: float | None = None) -> list[Chat]:
        now = time.time() if now is None else now
        folder = next((f for f in self.folders if f.id == folder_id), None)
        if folder and folder_id not in (ALL, ARCHIVE):
            chats = [c for c in self.chats.values() if folder.contains(c, now)]
            order = {chat_id: i for i, chat_id in enumerate(folder.pinned)}
            return sorted(chats, key=lambda c: (order.get(c.id, len(order)), -_when(c)))
        archived = folder_id == ARCHIVE
        chats = [c for c in self.chats.values() if c.archived == archived]
        return sorted(chats, key=lambda c: (c.pin or 1_000_000, -_when(c)))

    def tabs(self) -> list[tuple[int, str]]:
        """Folder tabs: (id, title)."""
        tabs = [(ALL, "All")] + [(f.id, f.title) for f in self.folders]
        if any(c.archived for c in self.chats.values()):
            tabs.append((ARCHIVE, "Archive"))
        return tabs

    def unread_chats(self, folder_id: int, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return sum(c.has_unread and not c.muted(now) for c in self.visible(folder_id, now))

    # -- writing

    def put_chats(self, chats: Iterable[Chat]) -> None:
        for chat in chats:
            old = self.chats.get(chat.id)
            if old and old.last and (chat.last is None or old.last.id > chat.last.id):
                # A live update overtook this page while it was on its way.
                chat = replace(chat, last=old.last, unread=old.unread, mentions=old.mentions)
            if old and old.read_inbox > chat.read_inbox:
                chat = replace(chat, read_inbox=old.read_inbox, unread=old.unread)
            self.chats[chat.id] = chat

    def forget_chats_except(self, seen: set[int]) -> None:
        """After a full reload: drop chats that were left or deleted."""
        for chat_id in [c for c in self.chats if c not in seen]:
            del self.chats[chat_id]

    def set_last(self, chat_id: int, message: Message | None) -> None:
        chat = self.chats.get(chat_id)
        if chat:
            self.chats[chat_id] = replace(chat, last=message)

    def apply(self, event: Event) -> Change:
        change = Change()
        match event:
            case MessageAdded():
                self._added(event, change)
            case MessageChanged():
                self._changed(event.message, change)
            case MessagesRemoved():
                self._removed(event, change)
            case InboxRead():
                self._inbox_read(event, change)
            case OutboxRead():
                chat = self.chats.get(event.chat_id)
                if chat and event.up_to > chat.read_outbox:
                    self._replace(chat, change, read_outbox=event.up_to)
                    change.messages.add(chat.id)
            case ChatFlags():
                chat = self.chats.get(event.chat_id)
                if chat:
                    values = {
                        name: value
                        for name in ("muted_until", "marked_unread", "archived")
                        if (value := getattr(event, name)) is not None
                    }
                    self._replace(chat, change, **values)
                else:
                    change.reload_chats = True
            case PinsChanged():
                change.reload_chats = True
            case FoldersChanged():
                change.reload_folders = True
        return change

    def _replace(self, chat: Chat, change: Change, **values) -> Chat:
        updated = replace(chat, **values)
        self.chats[chat.id] = updated
        change.chats = True
        return updated

    def _added(self, event: MessageAdded, change: Change) -> None:
        message = event.message
        chat = self.chats.get(message.chat_id) or event.chat
        if chat is None:
            change.reload_chats = True
            return
        history = self.histories.get(chat.id)
        fresh = True
        if history and history.loaded and history.at_end:
            fresh = history.put(message)
            change.messages.add(chat.id)
        newest = chat.last is None or message.id > chat.last.id
        values: dict = {}
        if newest:
            values["last"] = message
        counts = fresh and newest and not message.outgoing and message.id > chat.read_inbox
        if counts:
            values["unread"] = chat.unread + 1
            if message.mentioned:
                values["mentions"] = chat.mentions + 1
        self._replace(chat, change, **values)

    def _changed(self, message: Message, change: Change) -> None:
        history = self.histories.get(message.chat_id)
        if history and (old := history.get(message.id)):
            if message.reply and not message.reply.name and old.reply:
                message = replace(message, reply=old.reply)
            history.put(message)
            change.messages.add(message.chat_id)
        chat = self.chats.get(message.chat_id)
        if chat and chat.last and chat.last.id == message.id:
            self._replace(chat, change, last=message)

    def _removed(self, event: MessagesRemoved, change: Change) -> None:
        if event.chat_id is not None:
            candidates = [event.chat_id]
        else:  # ids are shared by all private chats and basic groups
            known = set(self.chats) | set(self.histories)
            candidates = [c for c in known if not is_channel_id(c)]
        ids = set(event.ids)
        for chat_id in candidates:
            history = self.histories.get(chat_id)
            removed = [m for i in ids if history and (m := history.remove(i))]
            if removed:
                change.messages.add(chat_id)
            chat = self.chats.get(chat_id)
            if chat is None:
                continue
            lost = sum(not m.outgoing and m.id > chat.read_inbox for m in removed)
            if lost:
                chat = self._replace(chat, change, unread=max(0, chat.unread - lost))
            if chat.last and chat.last.id in ids:
                if history and history.messages and history.at_end:
                    self._replace(chat, change, last=history.messages[-1])
                else:
                    change.stale.add(chat_id)

    def _inbox_read(self, event: InboxRead, change: Change) -> None:
        chat = self.chats.get(event.chat_id)
        if chat is None or event.up_to < chat.read_inbox:
            return
        unread = event.unread
        if unread is None:
            unread = self._unread_after(chat, event.up_to)
        values: dict = {"read_inbox": event.up_to, "unread": unread}
        if unread == 0:
            values |= {"mentions": 0, "marked_unread": False}
        self._replace(chat, change, **values)

    def _unread_after(self, chat: Chat, up_to: int) -> int:
        if chat.last and up_to >= chat.last.id:
            return 0
        history = self.histories.get(chat.id)
        if history and history.at_end and history.messages and history.messages[0].id <= up_to:
            return sum(not m.outgoing and m.id > up_to for m in history.messages)
        return chat.unread


def _when(chat: Chat) -> float:
    return chat.last.date.timestamp() if chat.last else 0.0
