"""Plain data passed between the backend and the UI. All text is already sanitized."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

FOREVER = float("inf")


@dataclass(frozen=True, slots=True)
class User:
    id: int
    name: str
    username: str = ""


class Kind(Enum):
    USER = "user"
    BOT = "bot"
    GROUP = "group"
    CHANNEL = "channel"
    SAVED = "saved"


@dataclass(frozen=True, slots=True)
class Span:
    """A styled slice of a message's text, in characters."""

    start: int
    end: int
    kind: str  # bold, italic, underline, strike, code, pre, spoiler, quote, link, mention, tag
    url: str = ""  # of a link; for "pre", the language


@dataclass(frozen=True, slots=True)
class Media:
    kind: str  # photo, video, voice, ...
    label: str  # "Photo 1280x720", ready to show
    id: int = 0  # 0 if there is nothing to download
    name: str = ""  # as sent, so not safe to use as it is
    mime: str = ""
    size: int = 0
    picture: bool = False  # there is something to show
    width: int = 0  # of that picture
    height: int = 0
    preview: bytes = b""  # a tiny blurred JPEG of it
    spoiler: bool = False
    locked: str = ""  # why it may not be saved


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file to send."""

    path: Path
    size: int
    as_photo: bool = False

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True, slots=True)
class Reaction:
    key: str  # the emoji, "custom:" and its id, or "paid"
    label: str  # what to show
    count: int = 1
    mine: bool = False


@dataclass(frozen=True, slots=True)
class ReactionChoice:
    key: str
    label: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class ReactionMenu:
    choices: tuple[ReactionChoice, ...] = ()
    most: int = 1  # how many reactions we may give one message


@dataclass(frozen=True, slots=True)
class Reply:
    id: int
    name: str = ""
    text: str = ""


class State(Enum):
    SENT = "sent"
    SENDING = "sending"
    WAITING = "waiting"  # will be tried again
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    chat_id: int
    date: datetime  # timezone-aware
    text: str = ""
    sender_id: int = 0
    sender: str = ""
    outgoing: bool = False
    spans: tuple[Span, ...] = ()
    media: Media | None = None
    reply: Reply | None = None
    forwarded: str = ""
    edited: bool = False
    service: bool = False  # `text` describes an event, e.g. "Sara joined"
    mentioned: bool = False
    silent: bool = False  # sent without sound
    reactions: tuple[Reaction, ...] = ()
    reactions_shared: bool = False  # sent to everybody alike, so `mine` is not known
    state: State = State.SENT
    trouble: str = ""  # why it is not sent


@dataclass(frozen=True, slots=True)
class Chat:
    id: int
    title: str
    kind: Kind
    last: Message | None = None
    unread: int = 0
    mentions: int = 0
    marked_unread: bool = False
    muted_until: float = 0.0  # Unix time; FOREVER if muted for good
    pin: int = 0  # 1 is the topmost pinned chat, 0 is not pinned
    archived: bool = False
    contact: bool = False
    read_inbox: int = 0  # everything up to this id has been read by us
    read_outbox: int = 0  # ... and by them
    username: str = ""
    can_write: bool = True
    can_delete_any: bool = False  # other people's messages, for everyone

    def muted(self, now: float) -> bool:
        return self.muted_until > now

    @property
    def has_unread(self) -> bool:
        return self.unread > 0 or self.marked_unread


@dataclass(frozen=True, slots=True)
class Found:
    """A search result."""

    message: Message
    chat: Chat | None = None  # set when the search spans chats


@dataclass(frozen=True, slots=True)
class Folder:
    id: int
    title: str
    pinned: tuple[int, ...] = ()
    include: frozenset[int] = field(default_factory=frozenset)
    exclude: frozenset[int] = field(default_factory=frozenset)
    contacts: bool = False
    non_contacts: bool = False
    groups: bool = False
    channels: bool = False
    bots: bool = False
    exclude_muted: bool = False
    exclude_read: bool = False
    exclude_archived: bool = False

    def contains(self, chat: Chat, now: float) -> bool:
        if chat.id in self.exclude:
            return False
        if chat.id in self.include or chat.id in self.pinned:
            return True
        if self.exclude_archived and chat.archived:
            return False
        if self.exclude_muted and chat.muted(now) and not chat.mentions:
            return False
        if self.exclude_read and not chat.has_unread:
            return False
        match chat.kind:
            case Kind.BOT:
                return self.bots
            case Kind.GROUP:
                return self.groups
            case Kind.CHANNEL:
                return self.channels
            case Kind.SAVED:
                return self.contacts
            case _:
                return self.contacts if chat.contact else self.non_contacts
