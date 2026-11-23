"""In-memory backend for tests and --demo. Touches neither network nor disk."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from itertools import count
from typing import Any, BinaryIO

from peyk.backend import sample, sample_files
from peyk.backend.base import (
    AuthStep,
    BackendError,
    CodeSent,
    ErrorKind,
    Event,
    EventListener,
    InboxRead,
    Link,
    LinkListener,
    MessageAdded,
    Progress,
    ReactionsChanged,
    Typing,
    Unsubscribe,
)
from peyk.files import Source
from peyk.models import (
    Attachment,
    Chat,
    Folder,
    Found,
    Kind,
    Media,
    Message,
    Reaction,
    ReactionMenu,
    Reply,
    Span,
    StickerPack,
    User,
)
from peyk.text import format as fmt
from peyk.text import markup
from peyk.text.match import fold, score

DEMO_CODE = "12345"
DEMO_PASSWORD = "demo"
_CHATTER = (
    (sample.GROUP, 51, "علی", "کسی هست؟ می\u200cخواهم یک سؤال بپرسم"),
    (sample.SARA, sample.SARA, "سارا محمدی", "راستی، فردا باران می\u200cآید ☔"),
    (sample.GROUP, 50, "Reza", "Tests pass on my machine."),
    (sample.MUTED, sample.MUTED, "Noisy Nick", "ping"),
)


class FakeBackend:
    def __init__(
        self,
        signed_in: bool = True,
        password: str = DEMO_PASSWORD,
        hint: str = "it is 'demo'",
        user: User | None = None,
        delay: float = 0.0,
        chatter: float = 0.0,
    ) -> None:
        """With `chatter`, a new message arrives every that many seconds."""
        self.signed_in = signed_in
        self.password = password
        self.hint = hint
        self.user = user or User(sample.ME, "مهمان Demo", "demo_user")
        self.delay = delay
        self.chatter = chatter
        self.link = Link.OFFLINE
        self.calls: list[tuple[str, str]] = []
        self.failures: dict[str, list[Exception]] = {}
        self.closed = False
        self._listeners: list[LinkListener] = []
        self._event_listeners: list[EventListener] = []
        self._talking: asyncio.Task[None] | None = None
        chats, self.messages, self.folder_list = sample.build()
        self.chat_map: dict[int, Chat] = {c.id: c for c in chats}
        self.strangers: list[Chat] = [Chat(60, "Zahra Contact", Kind.USER, contact=True)]
        self.tokens: set[int] = set()
        self.lose_next_reply = False  # the message arrives but we never hear of it
        self.sent_files: dict[int, bytes] = {}
        self._file_ids = count(9000)
        self.menus: dict[int, ReactionMenu] = {}  # where they differ from the usual
        self.sticker_list: list[StickerPack] = list(sample.STICKER_PACKS)
        self.gifs: tuple[Media, ...] = sample.GIFS

    def fail(self, method: str, *failures: Exception) -> None:
        """Make the next calls to `method` raise."""
        self.failures.setdefault(method, []).extend(failures)

    async def _step(self, method: str, argument: str = "") -> None:
        self.calls.append((method, argument))
        await asyncio.sleep(self.delay)
        if self.failures.get(method):
            raise self.failures[method].pop(0)

    def on_link(self, listener: LinkListener) -> Unsubscribe:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def on_event(self, listener: EventListener) -> Unsubscribe:
        self._event_listeners.append(listener)
        return lambda: self._event_listeners.remove(listener)

    def emit(self, event: Event) -> None:
        for listener in list(self._event_listeners):
            listener(event)

    def set_link(self, link: Link) -> None:
        self.link = link
        for listener in list(self._listeners):
            listener(link)

    async def connect(self) -> AuthStep:
        self.set_link(Link.CONNECTING)
        try:
            await self._step("connect")
        except BaseException:
            self.set_link(Link.OFFLINE)
            raise
        self.set_link(Link.ONLINE)
        return AuthStep.READY if self.signed_in else AuthStep.PHONE

    async def send_code(self, phone: str) -> CodeSent:
        await self._step("send_code", phone)
        return CodeSent("to your other Telegram apps", len(DEMO_CODE), can_resend=True)

    async def resend_code(self) -> CodeSent:
        await self._step("resend_code")
        return CodeSent("by SMS", len(DEMO_CODE))

    async def submit_code(self, code: str) -> AuthStep:
        await self._step("submit_code", code)
        if code != DEMO_CODE:
            raise BackendError(ErrorKind.CODE, "That code is not right.")
        if self.password:
            return AuthStep.PASSWORD
        self.signed_in = True
        return AuthStep.READY

    async def password_hint(self) -> str:
        await self._step("password_hint")
        return self.hint

    async def submit_password(self, password: str) -> AuthStep:
        await self._step("submit_password", password)
        if password != self.password:
            raise BackendError(ErrorKind.PASSWORD, "That password is not right.")
        self.signed_in = True
        return AuthStep.READY

    async def me(self) -> User:
        await self._step("me")
        if self.chatter and self._talking is None:
            self._talking = asyncio.create_task(self._talk())
        return self.user

    # -- chats and messages

    async def chats(self, limit: int | None = None) -> AsyncIterator[list[Chat]]:
        await self._step("chats", "" if limit is None else str(limit))
        found = list(self.chat_map.values())[:limit]
        for start in range(0, len(found), 6):
            yield found[start : start + 6]
            await asyncio.sleep(self.delay)

    async def folders(self) -> list[Folder]:
        await self._step("folders")
        return list(self.folder_list)

    async def history(
        self, chat_id: int, before: int = 0, after: int = 0, limit: int = 50
    ) -> list[Message]:
        place = f"after {after}" if after else str(before)
        await self._step("history", f"{chat_id}:{place}:{limit}")
        messages = self.messages.get(chat_id, [])
        if after:
            return [m for m in messages if m.id > after][:limit]
        return [m for m in messages if not before or m.id < before][-limit:]

    async def around(self, chat_id: int, message_id: int, limit: int = 50) -> list[Message]:
        await self._step("around", f"{chat_id}:{message_id}:{limit}")
        messages = self.messages.get(chat_id, [])
        older = [m for m in messages if m.id < message_id][-(limit - limit // 2) :]
        return older + [m for m in messages if m.id >= message_id][: limit // 2]

    async def mark_read(self, chat_id: int, up_to: int, mentions: bool = False) -> None:
        await self._step("mark_read", f"{chat_id}:{up_to}" + (":mentions" if mentions else ""))
        chat = self.chat_map[chat_id]
        unread = sum(not m.outgoing and m.id > up_to for m in self.messages[chat_id])
        self.chat_map[chat_id] = replace(chat, unread=unread, read_inbox=up_to)
        self.emit(InboxRead(chat_id, up_to, unread))

    # -- writing

    async def send(
        self,
        chat_id: int,
        text: str,
        spans: tuple[Span, ...],
        reply_to: int,
        token: int,
        uploaded: Any = None,
    ) -> Message | None:
        await self._step("send", f"{chat_id}:{text}" + (f":{reply_to}" if reply_to else ""))
        if token in self.tokens:
            return None
        self.tokens.add(token)
        original = self._find(chat_id, reply_to) if reply_to else None
        reply = None
        if original:
            name = "You" if original.outgoing else original.sender
            reply = Reply(original.id, name, fmt.body(original))
        message = self.receive(
            chat_id,
            text,
            sample.ME,
            "You",
            quiet=True,
            spans=spans,
            reply=reply,
            media=uploaded,
        )
        if self.lose_next_reply:
            self.lose_next_reply = False
            raise BackendError(ErrorKind.NETWORK, "Could not reach Telegram.")
        return message

    def _find(self, chat_id: int, message_id: int) -> Message | None:
        return next((m for m in self.messages.get(chat_id, []) if m.id == message_id), None)

    async def edit(
        self, chat_id: int, message_id: int, text: str, formatting: bool = True
    ) -> Message:
        await self._step("edit", f"{chat_id}:{message_id}:{text}")
        old = self._find(chat_id, message_id)
        if old is None:
            raise BackendError(ErrorKind.REFUSED, "That message no longer exists.")
        ranges = [(s.start, s.end) for s in old.spans]
        revised = markup.revise(old.text, ranges, text, formatting)
        spans = list(revised.spans)
        for span, place in zip(old.spans, revised.carried, strict=True):
            if place:
                spans.append(replace(span, start=place[0], end=place[1]))
        new = replace(old, text=revised.text, spans=tuple(spans), edited=True)
        messages = self.messages[chat_id]
        messages[messages.index(old)] = new
        return new

    async def delete(self, chat_id: int, ids: list[int], for_everyone: bool) -> None:
        who = "everyone" if for_everyone else "me"
        await self._step("delete", f"{chat_id}:{','.join(map(str, ids))}:{who}")
        self.messages[chat_id] = [m for m in self.messages[chat_id] if m.id not in ids]
        left = self.messages[chat_id]
        last = left[-1] if left else None
        self.chat_map[chat_id] = replace(self.chat_map[chat_id], last=last)

    # -- files

    async def upload(self, attachment: Attachment, progress: Progress) -> Media:
        await self._step("upload", attachment.name + (":photo" if attachment.as_photo else ""))
        source = Source(attachment.path)
        try:
            data = source.read()
        finally:
            source.close()
        await self._pretend(len(data), progress)
        file_id = next(self._file_ids)
        self.sent_files[file_id] = data
        name, size = attachment.name, len(data)
        if not attachment.as_photo:
            return Media("document", f"File {name} ({fmt.size(size)})", file_id, name, size=size)
        width, height = sample_files.size_of(data)
        return Media("photo", f"Photo {width}x{height}", file_id, size=size, picture=True,
                     width=width, height=height)  # fmt: skip

    async def _pretend(self, size: int, progress: Progress) -> None:
        steps = 10 if self.delay else 2
        for step in range(steps + 1):
            progress(size * step // steps, size)
            await asyncio.sleep(self.delay)

    def _content(self, chat_id: int, message_id: int) -> tuple[Media, bytes]:
        message = self._find(chat_id, message_id)
        if message is None or message.media is None or not message.media.id:
            raise BackendError(ErrorKind.REFUSED, "That message no longer exists.")
        media = message.media
        return media, self.sent_files.get(media.id) or sample_files.content(media)

    async def picture(self, chat_id: int, message_id: int, side: int) -> bytes | None:
        await self._step("picture", f"{chat_id}:{message_id}:{side}")
        media, data = self._content(chat_id, message_id)
        return data if media.kind == "photo" else sample_files.picture(media)

    async def download(
        self, chat_id: int, message_id: int, sink: BinaryIO, progress: Progress
    ) -> None:
        await self._step("download", f"{chat_id}:{message_id}")
        _, data = self._content(chat_id, message_id)
        await self._pretend(len(data), progress)
        sink.write(data)

    # -- stickers and GIFs

    async def sticker_packs(self) -> list[StickerPack]:
        await self._step("sticker_packs")
        return [
            p if p.key in ("recent", "faved") else replace(p, stickers=())
            for p in self.sticker_list
        ]

    async def stickers(self, pack: str) -> tuple[Media, ...]:
        await self._step("stickers", pack)
        found = next((p for p in self.sticker_list if p.key == pack), None)
        if found is None:
            raise BackendError(ErrorKind.REFUSED, "This sticker set is no longer installed.")
        return found.stickers

    async def saved_gifs(self) -> tuple[Media, ...]:
        await self._step("saved_gifs")
        return self.gifs

    async def file_picture(self, file_id: int, side: int) -> bytes | None:
        await self._step("file_picture", f"{file_id}:{side}")
        everything = [*self.gifs, *(s for p in self.sticker_list for s in p.stickers)]
        found = next((m for m in everything if m.id == file_id), None)
        if found is None:
            raise BackendError(ErrorKind.REFUSED, "That is no longer in your collection.")
        return sample_files.picture(found)

    # -- reactions and typing

    async def reaction_menu(self, chat_id: int) -> ReactionMenu:
        await self._step("reaction_menu", str(chat_id))
        return self.menus.get(chat_id, ReactionMenu(sample.REACTIONS))

    async def react(
        self, chat_id: int, message_id: int, keys: tuple[str, ...]
    ) -> tuple[Reaction, ...]:
        await self._step("react", f"{chat_id}:{message_id}:{','.join(keys)}")
        message = self._find(chat_id, message_id)
        if message is None:
            raise BackendError(ErrorKind.REFUSED, "That message no longer exists.")
        others = {r.key: r.count - r.mine for r in message.reactions}
        counts = others | {key: others.get(key, 0) + 1 for key in keys}
        given = sample.reacted(*((k, n, k in keys) for k, n in counts.items() if n))
        self.set_reactions(message, given)
        return given

    def set_reactions(self, message: Message, reactions: tuple[Reaction, ...]) -> None:
        messages = self.messages[message.chat_id]
        index = next(i for i, m in enumerate(messages) if m.id == message.id)
        messages[index] = replace(messages[index], reactions=reactions)

    async def refresh_reactions(self, chat_id: int, ids: list[int]) -> None:
        await self._step("refresh_reactions", f"{chat_id}:{','.join(map(str, ids))}")
        for message_id in ids:
            message = self._find(chat_id, message_id)
            if message:
                self.emit(ReactionsChanged(chat_id, message_id, message.reactions))

    async def typing(self, chat_id: int) -> None:
        await self._step("typing", str(chat_id))

    # -- searching

    async def find_chats(self, query: str) -> list[Chat]:
        await self._step("find_chats", query)
        return [c for c in self.strangers if score(query, c.title, c.username) is not None]

    async def members(self, chat_id: int, query: str) -> list[User]:
        await self._step("members", f"{chat_id}:{query}")
        people = {
            m.sender_id: User(m.sender_id, m.sender)
            for m in self.messages.get(chat_id, [])
            if not m.service and m.sender_id
        }
        return [u for u in people.values() if score(query, u.name) is not None]

    async def search(
        self, query: str, chat_id: int | None = None, sender_id: int | None = None
    ) -> AsyncIterator[list[Found]]:
        await self._step("search", f"{query}:{chat_id}:{sender_id}")
        hits = []
        for where, messages in self.messages.items():
            if chat_id in (None, where):
                for m in messages:
                    fits = not query or fold(query) in fold(m.text)
                    if fits and not m.service and sender_id in (None, m.sender_id):
                        chat = self.chat_map[where] if chat_id is None else None
                        hits.append(Found(m, chat))
        hits.sort(key=lambda hit: hit.message.date, reverse=True)
        for start in range(0, len(hits), 30):
            if start:
                await self._step("search_more", query)
            yield hits[start : start + 30]

    def receive(
        self,
        chat_id: int,
        text: str,
        sender_id: int = 99,
        sender: str = "Someone",
        quiet: bool = False,
        **extra,
    ) -> Message:
        """A new message arrives. `quiet`: while we are offline, so no event."""
        messages = self.messages.setdefault(chat_id, [])
        message = Message(
            id=messages[-1].id + 1 if messages else 1,
            chat_id=chat_id,
            date=datetime.now().astimezone(),
            text=text,
            sender_id=sender_id,
            sender=sender,
            outgoing=sender_id == sample.ME,
            **extra,
        )
        messages.append(message)
        chat = self.chat_map.get(chat_id)
        if chat:
            unread = chat.unread + (not message.outgoing)
            self.chat_map[chat_id] = replace(chat, last=message, unread=unread)
        if not quiet:
            self.emit(MessageAdded(message))
        return message

    async def _talk(self) -> None:
        turn = 0
        while True:
            await asyncio.sleep(self.chatter / 2)
            chat_id, sender_id, sender, text = _CHATTER[turn % len(_CHATTER)]
            self.emit(Typing(chat_id, sender_id, "typing", sender))
            await asyncio.sleep(self.chatter / 2)
            self.receive(chat_id, text, sender_id, sender)
            self._cheer()
            turn += 1

    def _cheer(self) -> None:
        """Someone likes our last message in the group."""
        ours = [m for m in self.messages[sample.GROUP] if m.outgoing]
        if ours and not ours[-1].reactions:
            given = sample.reacted(("🔥", 1, False))
            self.set_reactions(ours[-1], given)
            self.emit(ReactionsChanged(sample.GROUP, ours[-1].id, given))

    async def log_out(self) -> None:
        await self._step("log_out")
        self.signed_in = False
        self.set_link(Link.OFFLINE)

    async def close(self) -> None:
        self.closed = True
        self.link = Link.OFFLINE
        if self._talking:
            self._talking.cancel()
            self._talking = None
