"""Messages on their way out, in order, one chat at a time.

Files are the exception: what follows one does not wait for its upload.

Nothing typed is lost: a message stays here until Telegram confirms it. Each has
a token, so sending it again after an unclear failure cannot send it twice.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable, Coroutine
from dataclasses import replace
from datetime import datetime
from itertools import count
from typing import Any

from peyk.backend.base import Backend, BackendError, ErrorKind
from peyk.models import Attachment, Media, Message, Reply, Span, State
from peyk.text import format as fmt

FIRST_ID = 1 << 60  # sorts after every real message
FIRST_PAUSE, LONGEST_PAUSE = 5, 60  # seconds between tries after network trouble


class Outbox:
    def __init__(
        self,
        backend: Backend,
        start: Callable[[Coroutine], object],
        later: Callable[[float, Callable[[], None]], object],
        changed: Callable[[int], None],
        sent: Callable[[int, Message | None], None],
        progress: Callable[[int, int, int, int], None] = lambda *_: None,
    ) -> None:
        """`start` runs a coroutine in the background, `later` calls after some seconds."""
        self._backend = backend
        self._start = start
        self._later = later
        self._changed = changed
        self._sent = sent
        self._progress = progress
        self._queues: dict[int, list[Message]] = {}
        self._tokens: dict[int, int] = {}
        self._typed: dict[int, str] = {}
        self._tries: dict[int, int] = {}
        self._attachments: dict[int, Attachment] = {}
        self._ready: dict[int, Media] = {}  # stickers and GIFs: on Telegram already
        self._uploaded: dict[int, Any] = {}
        self._uploads: dict[int, asyncio.Future[Any]] = {}
        self._in_flight: set[int] = set()
        self._running: set[int] = set()
        self._ids = count(FIRST_ID)

    def waiting(self, chat_id: int) -> list[Message]:
        return self._queues.get(chat_id, [])

    def get(self, chat_id: int, message_id: int) -> Message | None:
        return next((m for m in self.waiting(chat_id) if m.id == message_id), None)

    @property
    def unsent(self) -> int:
        return sum(len(queue) for queue in self._queues.values())

    def add(
        self,
        chat_id: int,
        text: str,
        spans: tuple[Span, ...],
        reply: Reply | None,
        sender_id: int,
        typed: str,
        attachment: Attachment | None = None,
        ready: Media | None = None,
    ) -> Message:
        """`typed` is the text with its formatting marks. `ready`: a sticker or GIF."""
        message = Message(
            id=next(self._ids),
            chat_id=chat_id,
            date=datetime.now().astimezone(),
            text=text,
            spans=spans,
            sender_id=sender_id,
            sender="You",
            outgoing=True,
            reply=reply,
            media=_shown(attachment) if attachment else ready,
            state=State.SENDING,
        )
        if attachment:
            self._attachments[message.id] = attachment
        if ready:
            self._ready[message.id] = ready
        self._tokens[message.id] = secrets.randbits(63) or 1
        self._typed[message.id] = typed
        self._queues.setdefault(chat_id, []).append(message)
        self._changed(chat_id)
        self._go(chat_id)
        return message

    def discard(self, chat_id: int, message_id: int) -> Message | None:
        """None if it is too late."""
        message = self.get(chat_id, message_id)
        if message is None or message_id in self._in_flight:
            return None
        if upload := self._uploads.get(message_id):
            upload.cancel()
        self._forget(message)
        self._changed(chat_id)
        # It may have been holding up others.
        self._go(chat_id)
        return message

    def is_ready_file(self, message_id: int) -> bool:
        """A sticker or GIF, which cannot be taken back into the box."""
        return message_id in self._ready

    def take_back(
        self, chat_id: int, message_id: int
    ) -> tuple[str, Reply | None, Attachment | None] | None:
        """As it was typed, for editing."""
        typed = self._typed.get(message_id, "")
        attachment = self._attachments.get(message_id)
        message = self.discard(chat_id, message_id)
        return (typed, message.reply, attachment) if message else None

    def _forget(self, message: Message) -> None:
        queue = self._queues[message.chat_id]
        self._queues[message.chat_id] = [m for m in queue if m.id != message.id]
        self._tokens.pop(message.id, None)
        self._typed.pop(message.id, None)
        self._tries.pop(message.id, None)
        self._attachments.pop(message.id, None)
        self._uploaded.pop(message.id, None)
        self._ready.pop(message.id, None)

    def retry(self, chat_id: int, message_id: int) -> None:
        message = self.get(chat_id, message_id)
        if message and message.state is not State.SENDING:
            self._mark(message, State.SENDING)
            self._go(chat_id)

    def resume(self) -> None:
        """The connection is back."""
        for chat_id, queue in self._queues.items():
            for message in list(queue):
                if message.state is State.WAITING:
                    self._mark(message, State.SENDING)
            self._go(chat_id)

    def _mark(self, message: Message, state: State, trouble: str = "") -> None:
        queue = self._queues.get(message.chat_id, [])
        for i, other in enumerate(queue):
            if other.id == message.id:
                queue[i] = replace(message, state=state, trouble=trouble)
                self._changed(message.chat_id)

    def _go(self, chat_id: int) -> None:
        if chat_id not in self._running:
            self._running.add(chat_id)
            self._start(self._run(chat_id))

    async def _run(self, chat_id: int) -> None:
        try:
            while message := self._next(chat_id):
                await self._deliver(message)
        finally:
            self._running.discard(chat_id)

    def _next(self, chat_id: int) -> Message | None:
        for message in self.waiting(chat_id):
            if message.state is State.SENDING:
                if message.id in self._attachments and message.id not in self._uploaded:
                    self._begin_upload(message)
                    continue
                return message
            if message.state is State.WAITING:
                return None  # keep the order
        return None

    async def _deliver(self, message: Message) -> None:
        self._tries[message.id] = self._tries.get(message.id, 0) + 1
        self._in_flight.add(message.id)
        try:
            sent = await self._backend.send(
                message.chat_id,
                message.text,
                message.spans,
                message.reply.id if message.reply else 0,
                self._tokens[message.id],
                self._uploaded.get(message.id) or self._ready.get(message.id),
            )
        except BackendError as error:
            if error.kind not in (ErrorKind.NETWORK, ErrorKind.FLOOD):
                self._uploaded.pop(message.id, None)  # it may be what is wrong
            self._failed(message, error)
            return
        finally:
            self._in_flight.discard(message.id)
        self._forget(message)
        self._sent(message.chat_id, sent)

    def _begin_upload(self, message: Message) -> None:
        if not any(self.get(message.chat_id, other) for other in self._uploads):

            def progress(done: int, total: int) -> None:
                self._progress(message.chat_id, message.id, done, total)

            work = self._backend.upload(self._attachments[message.id], progress)
            self._uploads[message.id] = asyncio.ensure_future(work)
            self._tries[message.id] = self._tries.get(message.id, 0) + 1
            self._start(self._upload(message))

    async def _upload(self, message: Message) -> None:
        work = self._uploads[message.id]
        try:
            uploaded = await work
        except BackendError as error:
            self._failed(message, error)
        except asyncio.CancelledError:
            if self.get(message.chat_id, message.id) is not None:  # we are, not only it
                work.cancel()
                raise
        else:
            if self.get(message.chat_id, message.id) is not None:
                self._uploaded[message.id] = uploaded
        finally:
            self._uploads.pop(message.id, None)
        self._go(message.chat_id)

    def _failed(self, message: Message, error: BackendError) -> None:
        if error.kind is ErrorKind.NETWORK:
            self._mark(message, State.WAITING, "Waiting for the connection.")
            # Not only when the link comes back: a call can also fail while it is up.
            pause = FIRST_PAUSE * 2 ** min(self._tries[message.id] - 1, 10)
            self._try_later(message, min(pause, LONGEST_PAUSE))
        elif error.kind is ErrorKind.FLOOD and error.retry_after:
            note = f"{error.message} Trying again in {error.retry_after}s."
            self._mark(message, State.WAITING, note)
            self._try_later(message, error.retry_after)
        else:
            self._mark(message, State.FAILED, error.message)

    def _try_later(self, message: Message, seconds: float) -> None:
        tries = self._tries[message.id]

        def again() -> None:
            waiting = self.get(message.chat_id, message.id)
            # Unless it was tried again meanwhile: that has its own pause.
            if waiting and waiting.state is State.WAITING and self._tries[message.id] == tries:
                self.retry(message.chat_id, message.id)

        self._later(seconds, again)


def _shown(attachment: Attachment) -> Media:
    if attachment.as_photo:
        return Media("photo", f"Photo {attachment.name}")
    return Media("document", f"File {attachment.name} ({fmt.size(attachment.size)})")
