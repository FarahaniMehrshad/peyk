"""What the UI needs from a Telegram connection."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, BinaryIO, Protocol

from peyk.models import (
    Attachment,
    Chat,
    Folder,
    Found,
    Media,
    Message,
    Reaction,
    ReactionMenu,
    Span,
    StickerPack,
    User,
    VoiceNote,
)


class AuthStep(Enum):
    PHONE = auto()
    CODE = auto()
    PASSWORD = auto()
    READY = auto()


class Link(Enum):
    CONNECTING = "connecting"
    ONLINE = "online"
    OFFLINE = "offline"


class ErrorKind(Enum):
    NETWORK = auto()
    FLOOD = auto()
    PHONE = auto()
    CODE = auto()
    CODE_EXPIRED = auto()
    PASSWORD = auto()
    API_KEYS = auto()
    SESSION = auto()
    ACCOUNT = auto()
    REFUSED = auto()  # Telegram does not allow it
    DISK = auto()
    OTHER = auto()


class BackendError(Exception):
    """Something went wrong; `message` is ready to show to the user."""

    def __init__(self, kind: ErrorKind, message: str, retry_after: int = 0) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class CodeSent:
    where: str  # "in your Telegram app", "by SMS", ...
    length: int = 0  # 0 if unknown
    can_resend: bool = False


# -- live updates


@dataclass(frozen=True, slots=True)
class MessageAdded:
    message: Message
    chat: Chat | None = None  # set when the chat may be new to us


@dataclass(frozen=True, slots=True)
class MessageChanged:
    message: Message


@dataclass(frozen=True, slots=True)
class MessagesRemoved:
    chat_id: int | None  # None: some private chat or basic group
    ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ReactionsChanged:
    chat_id: int
    message_id: int
    reactions: tuple[Reaction, ...]
    shared: bool = False  # as in Message.reactions_shared


@dataclass(frozen=True, slots=True)
class Typing:
    """Someone is typing, or whatever `what` says. Empty: no longer."""

    chat_id: int
    user_id: int
    what: str = "typing"
    name: str = ""  # if known


@dataclass(frozen=True, slots=True)
class InboxRead:
    chat_id: int
    up_to: int
    unread: int | None = None  # what is still unread, if known


@dataclass(frozen=True, slots=True)
class OutboxRead:
    chat_id: int
    up_to: int


@dataclass(frozen=True, slots=True)
class ChatFlags:
    """Fields that are not None have changed."""

    chat_id: int
    muted_until: float | None = None
    marked_unread: bool | None = None
    archived: bool | None = None


@dataclass(frozen=True, slots=True)
class PinsChanged:
    """Of chats in the list."""


@dataclass(frozen=True, slots=True)
class MessagesPinned:
    chat_id: int
    ids: tuple[int, ...]
    pinned: bool  # False: unpinned


@dataclass(frozen=True, slots=True)
class FoldersChanged:
    pass


Event = (
    MessageAdded
    | MessageChanged
    | MessagesRemoved
    | ReactionsChanged
    | Typing
    | InboxRead
    | OutboxRead
    | ChatFlags
    | PinsChanged
    | MessagesPinned
    | FoldersChanged
)

LinkListener = Callable[[Link], None]
Unsubscribe = Callable[[], None]
EventListener = Callable[[Event], None]
Progress = Callable[[int, int], None]  # bytes so far, bytes in all (0 if unknown)


class Backend(Protocol):
    link: Link

    def on_link(self, listener: LinkListener) -> Unsubscribe: ...

    def on_event(self, listener: EventListener) -> Unsubscribe: ...

    async def connect(self) -> AuthStep: ...

    async def send_code(self, phone: str) -> CodeSent: ...

    async def resend_code(self) -> CodeSent: ...

    async def submit_code(self, code: str) -> AuthStep: ...

    async def password_hint(self) -> str: ...

    async def submit_password(self, password: str) -> AuthStep: ...

    async def me(self) -> User: ...

    def chats(self, limit: int | None = None) -> AsyncIterator[list[Chat]]:
        """Batches, most recent first. Pinned chats come first."""
        ...

    async def folders(self) -> list[Folder]: ...

    async def history(
        self, chat_id: int, before: int = 0, after: int = 0, limit: int = 50
    ) -> list[Message]:
        """The newest messages, or those next to a message id. Oldest first.

        Fewer than `limit` means there are no more in that direction.
        """
        ...

    async def around(self, chat_id: int, message_id: int, limit: int = 50) -> list[Message]:
        """Messages on both sides of one, oldest first."""
        ...

    async def mark_read(self, chat_id: int, up_to: int, mentions: bool = False) -> None:
        """With `mentions`, also mark every mention in the chat as seen."""
        ...

    async def send(
        self,
        chat_id: int,
        text: str,
        spans: tuple[Span, ...],
        reply_to: int,
        token: int,
        uploaded: Any = None,
    ) -> Message | None:
        """Trying again with the same token never sends twice; then it may return None.

        With `uploaded`, from `upload`, the text is the caption. It may also be the
        Media of a sticker or GIF from `sticker_packs`, `stickers` or `saved_gifs`.
        """
        ...

    async def upload(self, attachment: Attachment | VoiceNote, progress: Progress) -> Any: ...

    async def picture(self, chat_id: int, message_id: int, side: int) -> bytes | None:
        """The picture in a message, about `side` pixels or the largest there is."""
        ...

    async def download(
        self, chat_id: int, message_id: int, sink: BinaryIO, progress: Progress
    ) -> None: ...

    async def sticker_packs(self) -> list[StickerPack]:
        """Recent and favourite stickers with their stickers, then installed sets without."""
        ...

    async def stickers(self, pack: str) -> tuple[Media, ...]: ...

    async def saved_gifs(self) -> tuple[Media, ...]: ...

    async def file_picture(self, file_id: int, side: int) -> bytes | None:
        """The picture of a sticker or GIF from the lists above."""
        ...

    async def edit(
        self, chat_id: int, message_id: int, text: str, formatting: bool = True
    ) -> Message:
        """Keeps the formatting the message had. With `formatting`, typed marks add more."""
        ...

    async def delete(self, chat_id: int, ids: list[int], for_everyone: bool) -> None: ...

    async def reaction_menu(self, chat_id: int) -> ReactionMenu: ...

    async def react(
        self, chat_id: int, message_id: int, keys: tuple[str, ...]
    ) -> tuple[Reaction, ...]:
        """Make these our reactions to the message. Returns everyone's."""
        ...

    async def refresh_reactions(self, chat_id: int, ids: list[int]) -> None:
        """Ask for what may have changed; it arrives as events."""
        ...

    async def typing(self, chat_id: int) -> None:
        """Tell them that we are typing. It lasts for a few seconds."""
        ...

    async def forward(
        self, from_chat: int, message_id: int, to_chat: int, token: int, with_author: bool
    ) -> Message | None:
        """Like `send`: the same token never forwards twice; then it may return None."""
        ...

    async def pinned(self, chat_id: int) -> list[Message]:
        """The chat's pinned messages, newest first."""
        ...

    async def pin(
        self, chat_id: int, message_id: int, pinned: bool, silent: bool = False,
        only_me: bool = False,
    ) -> None:
        """`silent`: without telling the members. `only_me`: in a private chat."""
        ...

    async def mute(self, chat_id: int, until: float) -> None:
        """Until that Unix time; FOREVER, or 0 to unmute."""
        ...

    async def archive(self, chat_id: int, archived: bool) -> None: ...

    async def pin_chat(self, chat_id: int, pinned: bool) -> None: ...

    async def mark_unread(self, chat_id: int, unread: bool) -> None: ...

    async def find_chats(self, query: str) -> list[Chat]:
        """Contacts and chats the user knows, whether or not they are in the list."""
        ...

    async def members(self, chat_id: int, query: str) -> list[User]: ...

    def search(
        self, query: str, chat_id: int | None = None, sender_id: int | None = None
    ) -> AsyncIterator[list[Found]]:
        """Batches, newest first."""
        ...

    async def log_out(self) -> None:
        """Revoke the session, then delete it. Keeps it if revoking fails."""
        ...

    async def close(self) -> None: ...
