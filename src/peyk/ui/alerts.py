"""Which messages are worth a notification."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from peyk.backend.base import Event, InboxRead, MessageAdded, MessagesRemoved
from peyk.models import Chat, Message
from peyk.platform.notify import Notifier
from peyk.store import Store, is_channel_id
from peyk.text import format as fmt

PAUSE = 3.0  # seconds between two notifications of one chat
TOO_OLD = timedelta(minutes=10)  # what we catch up on is no news


@dataclass(slots=True)
class _Alert:
    last: Message
    count: int = 1


class Alerts:
    def __init__(
        self,
        notifier: Notifier,
        store: Store,
        set_timer: Callable[[float, Callable[[], None]], Any],
        in_view: Callable[[int], bool],
        show_content: bool = True,
    ) -> None:
        """`in_view`: is the user looking at this chat?"""
        self._notifier = notifier
        self._store = store
        self._set_timer = set_timer
        self._in_view = in_view
        self._show_content = show_content
        self._alerts: dict[int, _Alert] = {}
        self._resting: set[int] = set()  # notified a moment ago
        self._waiting: set[int] = set()  # more came meanwhile

    def event(self, event: Event) -> None:
        """After the store has taken it in."""
        match event:
            case MessageAdded():
                chat = self._store.chats.get(event.message.chat_id)
                if chat and self._wanted(event.message, chat):
                    self._add(event.message)
            case InboxRead():
                alert = self._alerts.get(event.chat_id)
                if alert and event.up_to >= alert.last.id:
                    self.forget(event.chat_id)
            case MessagesRemoved():
                for chat_id, alert in list(self._alerts.items()):
                    same = event.chat_id == chat_id
                    if event.chat_id is None:
                        same = not is_channel_id(chat_id)
                    if same and alert.last.id in event.ids:
                        self.forget(chat_id)

    def forget(self, chat_id: int) -> None:
        self._waiting.discard(chat_id)
        if self._alerts.pop(chat_id, None):
            self._notifier.forget(chat_id)

    def _wanted(self, message: Message, chat: Chat) -> bool:
        alert = self._alerts.get(chat.id)
        return not (
            message.outgoing
            or message.service
            or message.id <= chat.read_inbox
            or (alert and message.id <= alert.last.id)
            or chat.muted(time.time())
            or datetime.now(UTC) - message.date > TOO_OLD
            or self._in_view(chat.id)
        )

    def _add(self, message: Message) -> None:
        chat_id = message.chat_id
        alert = self._alerts.get(chat_id)
        if alert:
            alert.last, alert.count = message, alert.count + 1
        else:
            self._alerts[chat_id] = _Alert(message)
        if chat_id in self._resting:
            self._waiting.add(chat_id)
        else:
            self._notify(chat_id)

    def _notify(self, chat_id: int) -> None:
        alert, chat = self._alerts.get(chat_id), self._store.chats.get(chat_id)
        if not alert or not chat:
            return
        self._resting.add(chat_id)
        self._set_timer(PAUSE, lambda: self._rested(chat_id))
        many = alert.count > 1
        if self._show_content:
            title = f"{chat.title} ({alert.count})" if many else chat.title
            text = fmt.notice(alert.last, chat)
        else:
            title = chat.title
            text = f"{alert.count} new messages" if many else "New message"
        self._notifier.show(chat_id, title, text, alert.last.silent)

    def _rested(self, chat_id: int) -> None:
        self._resting.discard(chat_id)
        if chat_id in self._waiting:
            self._waiting.discard(chat_id)
            self._notify(chat_id)
