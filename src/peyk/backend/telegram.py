"""The real backend. The only module that talks to Telethon's client."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, TypeVar

from telethon import TelegramClient, errors, events, functions, types, utils
from telethon.network.connection import (
    ConnectionTcpFull,
    ConnectionTcpMTProxyRandomizedIntermediate,
)

from peyk import __version__, images
from peyk.backend import convert
from peyk.backend.base import (
    AuthStep,
    BackendError,
    ChatFlags,
    CodeSent,
    ErrorKind,
    Event,
    EventListener,
    FoldersChanged,
    InboxRead,
    Link,
    LinkListener,
    MessageAdded,
    MessageChanged,
    MessagesRemoved,
    OutboxRead,
    PinsChanged,
    Progress,
    Unsubscribe,
)
from peyk.backend.proxy import Proxy
from peyk.backend.session import EncryptedSession, SessionUnreadable, remove_session
from peyk.files import Source, safe_name
from peyk.loop import use_lazy_tasks
from peyk.models import Attachment, Chat, Folder, Found, Kind, Message, Span, User
from peyk.text import markup
from peyk.text.sanitize import sanitize_line

log = logging.getLogger(__name__)
T = TypeVar("T")

CONNECT_TIMEOUT = 30
CALL_TIMEOUT = 30
FLOOD_SLEEP = 15  # shorter waits are absorbed silently once signed in
BATCH = 100
SEARCH_BATCH = 30
STALL = 60  # seconds without progress before a transfer is given up
MAX_REMEMBERED = 2000  # messages with files
_OFFLINE = "Could not reach Telegram. Check your connection or proxy."

_CODE_PLACES: dict[type, str] = {
    types.auth.SentCodeTypeApp: "to your other Telegram apps",
    types.auth.SentCodeTypeSms: "by SMS",
    types.auth.SentCodeTypeSmsWord: "by SMS, as a word",
    types.auth.SentCodeTypeSmsPhrase: "by SMS, as a phrase",
    types.auth.SentCodeTypeFirebaseSms: "by SMS",
    types.auth.SentCodeTypeCall: "by phone call",
    types.auth.SentCodeTypeFlashCall: "by a short phone call",
    types.auth.SentCodeTypeMissedCall: "by a missed call (the last digits of its number)",
    types.auth.SentCodeTypeEmailCode: "by email",
    types.auth.SentCodeTypeFragmentSms: "to your Fragment account",
}


_NO_WRITING = "You cannot write in this chat."
_NO_FILES = "This chat does not allow files of this kind."
_BAD_PHOTO = "Telegram cannot use this picture as a photo. Send it as a file."
_LOST_PARTS = "The upload was incomplete."
_REFUSALS: dict[type, str] = {
    errors.ChatWriteForbiddenError: _NO_WRITING,
    errors.ChatSendPlainForbiddenError: _NO_WRITING,
    errors.ChatRestrictedError: _NO_WRITING,
    errors.ChatGuestSendForbiddenError: "You have to join this group to write in it.",
    errors.UserBannedInChannelError: "You are not allowed to write in public groups.",
    errors.ChannelPrivateError: "You have no access to this chat.",
    errors.ChatAdminRequiredError: "Only admins of this chat can do that.",
    errors.UserIsBlockedError: "This person does not accept your messages.",
    errors.YouBlockedUserError: "You have blocked this person.",
    errors.InputUserDeactivatedError: "This account was deleted.",
    errors.PeerFloodError: "Telegram limits messages from your account to strangers.",
    errors.MessageTooLongError: "The message is too long.",
    errors.MediaCaptionTooLongError: "The caption is too long.",
    errors.ChatSendMediaForbiddenError: _NO_FILES,
    errors.ChatSendPhotosForbiddenError: _NO_FILES,
    errors.ChatSendVideosForbiddenError: _NO_FILES,
    errors.ChatSendGifsForbiddenError: _NO_FILES,
    errors.PhotoInvalidDimensionsError: _BAD_PHOTO,
    errors.PhotoExtInvalidError: _BAD_PHOTO,
    errors.PhotoInvalidError: _BAD_PHOTO,
    errors.PhotoSaveFileInvalidError: _BAD_PHOTO,
    errors.FilePartsInvalidError: "The file is too large for Telegram.",
    errors.FilePartMissingError: _LOST_PARTS,
    errors.FilePart0MissingError: _LOST_PARTS,
    errors.MessageAuthorRequiredError: "You can only edit your own messages.",
    errors.MessageEditTimeExpiredError: "This message is too old to edit.",
    errors.MessageIdInvalidError: "That message no longer exists.",
    errors.MessageDeleteForbiddenError: "You cannot delete this message for everyone.",
}
# Codes that Telethon has no class for.
_REFUSAL_CODES: dict[str, str] = {
    "REPLY_MESSAGE_ID_INVALID": "The message you are replying to was deleted.",
    "PRIVACY_PREMIUM_REQUIRED": "This person only accepts messages from Premium users.",
    "CHAT_SEND_WEBPAGE_FORBIDDEN": "Links are not allowed in this chat.",
    "CHAT_SEND_DOCS_FORBIDDEN": _NO_FILES,
    "CHAT_SEND_AUDIOS_FORBIDDEN": _NO_FILES,
    "IMAGE_PROCESS_FAILED": _BAD_PHOTO,
}
_STALE_REFERENCE = (
    errors.FileReferenceExpiredError,
    errors.FileReferenceInvalidError,
    errors.FileReferenceEmptyError,
    errors.FilerefUpgradeNeededError,
)


@dataclass(frozen=True, slots=True)
class Uploaded:
    handle: Any
    attachment: Attachment
    as_photo: bool


def make_client(path: Path, api_id: int, api_hash: str, proxy: Proxy | None) -> TelegramClient:
    options: dict[str, Any] = {"connection": ConnectionTcpFull}
    if proxy and proxy.kind == "mtproxy":
        options["connection"] = ConnectionTcpMTProxyRandomizedIntermediate
        # Always with the dd prefix: Telethon strips it, and would otherwise strip
        # a plain secret that happens to start with dd or ee.
        options["proxy"] = (proxy.host, proxy.port, "dd" + proxy.secret)
    elif proxy:
        options["proxy"] = (
            proxy.kind,
            proxy.host,
            proxy.port,
            True,
            proxy.username or None,
            proxy.password or None,
        )
    return TelegramClient(
        EncryptedSession(path),
        api_id,
        api_hash,
        connection_retries=3,
        retry_delay=1,
        timeout=10,
        flood_sleep_threshold=0,  # tell the user instead of silently waiting
        device_model="Peyk",
        system_version="Windows",
        app_version=__version__,
        base_logger=logging.getLogger("telethon"),
        **options,
    )


class TelegramBackend:
    def __init__(
        self,
        session_path: Path,
        api_id: int,
        api_hash: str,
        proxy: Proxy | None = None,
        client_factory: Callable[..., Any] = make_client,
    ) -> None:
        self._path = session_path
        self._api_id = api_id
        self._api_hash = api_hash
        self._proxy = proxy
        self._factory = client_factory
        self._client: Any = None
        self._phone = ""
        self._code_hash = ""
        self._watcher: asyncio.Task[None] | None = None
        self._listeners: list[LinkListener] = []
        self._event_listeners: list[EventListener] = []
        self._me = 0
        self._known: set[int] = set()
        self._default_mute: dict[Kind, float] = {}
        # Raw messages that have files: these carry what it takes to fetch them.
        self._raws: OrderedDict[tuple[int, int], Any] = OrderedDict()
        self.link = Link.OFFLINE

    # -- connection ---------------------------------------------------------

    def on_link(self, listener: LinkListener) -> Unsubscribe:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _set_link(self, link: Link) -> None:
        if link is not self.link:
            self.link = link
            for listener in list(self._listeners):
                listener(link)

    async def connect(self) -> AuthStep:
        use_lazy_tasks()
        await self._stop_watching()
        self._set_link(Link.CONNECTING)
        log.info("connecting, proxy: %s", self._proxy.kind if self._proxy else "none")
        try:
            with _translated():
                if self._client is None:
                    self._client = self._factory(
                        self._path, self._api_id, self._api_hash, self._proxy
                    )
                    self._subscribe(self._client)
                async with asyncio.timeout(CONNECT_TIMEOUT):
                    await self._client.connect()
                    ready = await self._authorized()
        except BaseException as error:
            log.info("connecting failed: %s", type(error.__cause__ or error).__name__)
            self._set_link(Link.OFFLINE)
            await self._drop_client()
            if self._proxy and isinstance(error, BackendError) and error.kind is ErrorKind.NETWORK:
                error.message = f"Could not reach Telegram through {self._proxy.describe()}."
            raise
        log.info("connected, signed in: %s", ready)
        self._set_link(Link.ONLINE)
        self._watcher = asyncio.create_task(self._watch())
        return AuthStep.READY if ready else AuthStep.PHONE

    async def _authorized(self) -> bool:
        # Telethon's own check reports any server error as "not signed in".
        try:
            await self._client(functions.updates.GetStateRequest())
        except (errors.UnauthorizedError, errors.AuthKeyError):
            return False
        return True

    async def _watch(self) -> None:
        """Report link changes, and reconnect when Telethon stops trying."""
        gone = self._client.disconnected
        delay = 1
        while True:
            await asyncio.sleep(1)
            if not gone.done():
                self._set_link(Link.ONLINE if self._transport_up() else Link.CONNECTING)
                delay = 1
                continue
            if not gone.cancelled():
                gone.exception()  # retrieved, so asyncio does not complain
            if self._client.is_connected():  # it only moved to another data centre
                gone = self._client.disconnected
                continue
            self._set_link(Link.OFFLINE)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
            self._set_link(Link.CONNECTING)
            try:
                async with asyncio.timeout(CONNECT_TIMEOUT):
                    await self._client.connect()
            except Exception as error:
                log.info("reconnect failed: %s", type(error).__name__)
                continue
            gone = self._client.disconnected

    def _transport_up(self) -> bool:
        # Telethon reports "connected" while it is retrying; the sender knows better.
        sender = getattr(self._client, "_sender", None)
        probe = getattr(sender, "_transport_connected", None)
        return bool(probe()) if probe else bool(self._client.is_connected())

    async def _stop_watching(self) -> None:
        if self._watcher:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
            self._watcher = None

    async def _drop_client(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            async with asyncio.timeout(5):
                await client.disconnect()
        except Exception as error:
            log.info("disconnect failed: %s", type(error).__name__)

    async def close(self) -> None:
        await self._stop_watching()
        await self._drop_client()
        self._set_link(Link.OFFLINE)

    # -- signing in ---------------------------------------------------------

    async def _call(self, work: Awaitable[T]) -> T:
        with _translated():
            async with asyncio.timeout(CALL_TIMEOUT):
                return await work

    def _need_client(self) -> Any:
        if self._client is None:
            raise BackendError(ErrorKind.NETWORK, _OFFLINE)
        return self._client

    async def send_code(self, phone: str) -> CodeSent:
        client = self._need_client()
        request = functions.auth.SendCodeRequest(
            phone, self._api_id, self._api_hash, types.CodeSettings()
        )
        try:
            sent = await self._call(client(request))
        except BackendError as error:
            if not isinstance(error.__cause__, errors.AuthRestartError):
                raise
            sent = await self._call(client(request))
        self._phone = phone
        return self._code_sent(sent)

    async def resend_code(self) -> CodeSent:
        client = self._need_client()
        request = functions.auth.ResendCodeRequest(self._phone, self._code_hash)
        return self._code_sent(await self._call(client(request)))

    def _code_sent(self, sent: Any) -> CodeSent:
        kind = getattr(sent, "type", None)
        if kind is None or isinstance(kind, types.auth.SentCodeTypeSetUpEmailRequired):
            raise BackendError(
                ErrorKind.ACCOUNT,
                "Telegram wants an extra step that Peyk does not support. "
                "Sign in once with an official app, then try again.",
            )
        self._code_hash = sent.phone_code_hash or ""
        return CodeSent(
            where=_CODE_PLACES.get(type(kind), "by Telegram"),
            length=getattr(kind, "length", 0) or 0,
            can_resend=getattr(sent, "next_type", None) is not None,
        )

    async def submit_code(self, code: str) -> AuthStep:
        client = self._need_client()
        try:
            await self._call(
                client.sign_in(self._phone, code, phone_code_hash=self._code_hash)
            )
        except BackendError as error:
            if isinstance(error.__cause__, errors.SessionPasswordNeededError):
                return AuthStep.PASSWORD
            raise
        return AuthStep.READY

    async def password_hint(self) -> str:
        client = self._need_client()
        result = await self._call(client(functions.account.GetPasswordRequest()))
        return sanitize_line(getattr(result, "hint", None) or "")

    async def submit_password(self, password: str) -> AuthStep:
        client = self._need_client()
        await self._call(client.sign_in(password=password))
        return AuthStep.READY

    async def me(self) -> User:
        client = self._need_client()
        user = await self._call(client.get_me())
        if user is None:
            raise BackendError(ErrorKind.SESSION, "Telegram signed this session out.")
        self._me = user.id
        client.flood_sleep_threshold = FLOOD_SLEEP
        name = " ".join(part for part in (user.first_name, user.last_name) if part)
        return User(user.id, sanitize_line(name) or "Telegram user",
                    sanitize_line(user.username or ""))  # fmt: skip

    # -- chats and messages --------------------------------------------------

    async def chats(self, limit: int | None = None) -> AsyncIterator[list[Chat]]:
        client = self._need_client()
        await self._load_default_mute()
        dialogs = client.iter_dialogs(limit, ignore_migrated=True).__aiter__()
        batch: list[Chat] = []
        pins = 0
        while (dialog := await self._call(anext(dialogs, None))) is not None:
            pins += bool(dialog.pinned)
            pin = pins if dialog.pinned else 0
            chat = convert.chat_from_dialog(dialog, self._me, self._default_mute, pin)
            if chat is None:
                continue
            self._known.add(chat.id)
            batch.append(chat)
            if len(batch) >= BATCH:
                yield batch
                batch = []
        if batch:
            yield batch

    async def _load_default_mute(self) -> None:
        if self._default_mute:
            return
        client = self._need_client()
        scopes = (
            (types.InputNotifyUsers(), (Kind.USER, Kind.BOT)),
            (types.InputNotifyChats(), (Kind.GROUP,)),
            (types.InputNotifyBroadcasts(), (Kind.CHANNEL,)),
        )
        for peer, kinds in scopes:
            try:
                request = functions.account.GetNotifySettingsRequest(peer)
                settings = await self._call(client(request))
            except BackendError as error:
                log.info("no default notification settings: %s", error.kind.name)
                return
            for kind in kinds:
                self._default_mute[kind] = convert.muted_until(settings)

    async def folders(self) -> list[Folder]:
        client = self._need_client()
        result = await self._call(client(functions.messages.GetDialogFiltersRequest()))
        found = (convert.folder(raw, self._me) for raw in getattr(result, "filters", result))
        return [folder for folder in found if folder]

    async def history(
        self, chat_id: int, before: int = 0, after: int = 0, limit: int = 50
    ) -> list[Message]:
        client = self._need_client()
        if after:
            work = client.get_messages(chat_id, limit=limit, min_id=after, reverse=True)
        else:
            work = client.get_messages(chat_id, limit=limit, offset_id=before)
        return await self._converted(chat_id, await self._call(work))

    async def around(self, chat_id: int, message_id: int, limit: int = 50) -> list[Message]:
        client = self._need_client()
        # A negative offset moves towards newer messages.
        work = client.get_messages(
            chat_id, limit=limit, offset_id=message_id, add_offset=-(limit // 2)
        )
        return await self._converted(chat_id, await self._call(work))

    async def _converted(self, chat_id: int, raw: Iterable[Any]) -> list[Message]:
        found = sorted((m for m in raw if m is not None), key=lambda m: m.id)
        self._remember(chat_id, found)
        return await self._with_replies(chat_id, [convert.message(m, chat_id) for m in found])

    def _remember(self, chat_id: int, raws: Iterable[Any]) -> None:
        for raw in raws:
            if getattr(raw, "media", None) is not None:
                self._raws[chat_id, raw.id] = raw
                self._raws.move_to_end((chat_id, raw.id))
        while len(self._raws) > MAX_REMEMBERED:
            self._raws.popitem(last=False)

    async def _with_replies(self, chat_id: int, messages: list[Message]) -> list[Message]:
        """Fill in what replies refer to, fetching originals outside the page."""
        known = {m.id: m for m in messages}
        wanted = {m.reply.id for m in messages if m.reply} - set(known)
        if wanted:
            try:
                found = await self._call(
                    self._need_client().get_messages(chat_id, ids=sorted(wanted))
                )
                known |= {m.id: convert.message(m, chat_id) for m in found if m is not None}
            except BackendError as error:
                log.info("replied-to messages unavailable: %s", error.kind.name)
        filled = []
        for message in messages:
            if message.reply:
                original = known.get(message.reply.id)
                reply = (
                    convert.reply_to(original, message.reply.text)
                    if original
                    else replace(message.reply, text=message.reply.text or "Deleted message")
                )
                message = replace(message, reply=reply)
            filled.append(message)
        return filled

    async def mark_read(self, chat_id: int, up_to: int, mentions: bool = False) -> None:
        client = self._need_client()
        work = client.send_read_acknowledge(chat_id, max_id=up_to, clear_mentions=mentions)
        await self._call(work)

    # -- writing -------------------------------------------------------------

    async def send(
        self,
        chat_id: int,
        text: str,
        spans: tuple[Span, ...],
        reply_to: int,
        token: int,
        uploaded: Any = None,
    ) -> Message | None:
        # Not client.send_message: it would pick a new random id for every attempt,
        # and one that was only slow to be confirmed would be sent twice.
        client = self._need_client()
        peer = await self._peer(chat_id)
        common: dict[str, Any] = {
            "peer": peer,
            "message": text,
            "entities": convert.entities(text, spans),
            "reply_to": types.InputReplyToMessage(reply_to) if reply_to else None,
            "random_id": token,
        }
        request = (
            functions.messages.SendMediaRequest(media=_input_media(uploaded), **common)
            if uploaded
            else functions.messages.SendMessageRequest(**common)
        )
        try:
            result = await self._call(client(request))
        except BackendError as error:
            if isinstance(error.__cause__, errors.RandomIdDuplicateError):
                return None
            raise
        if isinstance(result, types.UpdateShortSentMessage):
            raw = types.Message(
                id=result.id,
                peer_id=utils.get_peer(peer),
                message=text,
                date=result.date,
                out=result.out,
                media=result.media,
                entities=result.entities,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=reply_to) if reply_to else None,
            )
            raw._finish_init(client, {}, peer)
        else:
            raw = client._get_response_message(request, result, peer)
        return await self._own_message(chat_id, raw) if raw else None

    async def _peer(self, chat_id: int) -> Any:
        return await self._call(self._need_client().get_input_entity(chat_id))

    async def _own_message(self, chat_id: int, raw: Any) -> Message:
        self._remember(chat_id, [raw])
        message = convert.message(raw, chat_id)
        if not message.sender_id:
            message = replace(message, sender_id=self._me, sender="You")
        (message,) = await self._with_replies(chat_id, [message])
        return message

    async def edit(
        self, chat_id: int, message_id: int, text: str, formatting: bool = True
    ) -> Message:
        client = self._need_client()
        raw = await self._call(client.get_messages(chat_id, ids=message_id))
        if raw is None:
            raise BackendError(ErrorKind.REFUSED, "That message no longer exists.")
        kept, ranges = convert.own_ranges(raw.message or "", raw.entities)
        revised = markup.revise(raw.message or "", ranges, text, formatting)
        entities = convert.entities(revised.text, revised.spans)
        for entity, place in zip(kept, revised.carried, strict=True):
            if place:
                entities.append(convert.moved(entity, revised.text, place))
        entities.sort(key=lambda e: (e.offset, -e.length))
        links = (types.MessageEntityUrl, types.MessageEntityTextUrl)
        had_link = any(isinstance(e, links) for e in raw.entities or ())
        peer = await self._peer(chat_id)
        request = functions.messages.EditMessageRequest(
            peer,
            message_id,
            message=revised.text,
            entities=entities,
            no_webpage=True if had_link and raw.media is None else None,
        )
        try:
            result = await self._call(client(request))
        except BackendError as error:
            if not isinstance(error.__cause__, errors.MessageNotModifiedError):
                raise
            edited = raw
        else:
            edited = client._get_response_message(request, result, peer)
            if edited is None:
                edited = await self._call(client.get_messages(chat_id, ids=message_id)) or raw
        return await self._own_message(chat_id, edited)

    async def delete(self, chat_id: int, ids: list[int], for_everyone: bool) -> None:
        client = self._need_client()
        await self._call(client.delete_messages(chat_id, ids, revoke=for_everyone))

    # -- files ---------------------------------------------------------------

    async def upload(self, attachment: Attachment, progress: Progress) -> Uploaded:
        client = self._need_client()
        photo = None
        if attachment.as_photo:
            photo = await asyncio.to_thread(images.as_photo, attachment.path)
        if photo:
            handle = await self._watched(
                lambda tick: client.upload_file(
                    photo, file_name="photo.jpg", progress_callback=tick
                ),
                progress,
            )
            return Uploaded(handle, attachment, True)
        source = Source(attachment.path)
        try:
            handle = await self._watched(
                lambda tick: client.upload_file(
                    source, file_size=source.size, file_name=source.name, progress_callback=tick
                ),
                progress,
            )
        finally:
            source.close()
        return Uploaded(handle, attachment, False)

    async def picture(self, chat_id: int, message_id: int, side: int) -> bytes | None:
        async def fetch(raw: Any, tick: Progress) -> bytes | None:
            client = self._need_client()
            whole = raw.document and raw.file.mime_type in convert.PICTURE_TYPES
            small_enough = (raw.file.size or 0) <= convert.MAX_PICTURE if raw.file else False
            thing = raw.photo or raw.document
            sizes = (thing.sizes if raw.photo else thing.thumbs) or () if thing else ()
            size = _best_size(sizes, side)
            enough = size is not None and max(size.w, size.h) >= side
            if whole and small_enough and not enough and (size is None or side > 400):
                return await client.download_media(raw, bytes, progress_callback=tick)
            if size is None:
                return None
            # By its letter: Telethon does not take a progressive size itself.
            thumb = size.type if raw.photo else size
            if not raw.photo and isinstance(size, types.PhotoSizeProgressive):
                return None
            return await client.download_media(raw, bytes, thumb=thumb, progress_callback=tick)

        return await self._transfer(chat_id, message_id, fetch, lambda done, total: None)

    async def download(
        self, chat_id: int, message_id: int, sink: BinaryIO, progress: Progress
    ) -> None:
        async def fetch(raw: Any, tick: Progress) -> None:
            sink.seek(0)  # it may be the second attempt
            sink.truncate()
            done = await self._need_client().download_media(raw, sink, progress_callback=tick)
            if done is None:
                raise BackendError(ErrorKind.REFUSED, "There is nothing to download.")

        await self._transfer(chat_id, message_id, fetch, progress)

    async def _transfer(
        self,
        chat_id: int,
        message_id: int,
        fetch: Callable[[Any, Progress], Awaitable[T]],
        progress: Progress,
    ) -> T:
        """References to files expire; then the message has to be fetched again."""
        raw = self._raws.get((chat_id, message_id))
        if raw is not None:
            try:
                return await self._watched(lambda tick: fetch(raw, tick), progress)
            except BackendError as error:
                if not isinstance(error.__cause__, _STALE_REFERENCE):
                    raise
        fresh = await self._call(self._need_client().get_messages(chat_id, ids=message_id))
        if fresh is None:
            raise BackendError(ErrorKind.REFUSED, "That message no longer exists.")
        self._remember(chat_id, [fresh])
        return await self._watched(lambda tick: fetch(fresh, tick), progress)

    async def _watched(self, start: Callable[[Progress], Awaitable[T]], progress: Progress) -> T:
        """However long it takes, as long as it makes progress."""
        with _translated():
            async with asyncio.timeout(STALL) as limit:

                def tick(done: int, total: int) -> None:
                    limit.reschedule(asyncio.get_running_loop().time() + STALL)
                    progress(int(done), int(total or 0))

                return await start(tick)

    # -- searching -----------------------------------------------------------

    async def find_chats(self, query: str) -> list[Chat]:
        client = self._need_client()
        result = await self._call(client(functions.contacts.SearchRequest(query, limit=20)))
        entities = {utils.get_peer_id(e): e for e in (*result.users, *result.chats)}
        found = []
        for peer in result.my_results:
            entity = entities.get(utils.get_peer_id(peer))
            chat = convert.chat_from_entity(entity, self._me, self._default_mute)
            if chat:
                found.append(chat)
        return found

    async def members(self, chat_id: int, query: str) -> list[User]:
        client = self._need_client()
        try:
            people = await self._call(client.get_participants(chat_id, limit=50, search=query))
        except BackendError as error:
            if error.kind is ErrorKind.REFUSED:  # members of channels are private
                return []
            raise
        return [User(p.id, convert.name_of(p), convert.username_of(p)) for p in people]

    async def search(
        self, query: str, chat_id: int | None = None, sender_id: int | None = None
    ) -> AsyncIterator[list[Found]]:
        client = self._need_client()
        messages = client.iter_messages(
            chat_id, search=query, from_user=sender_id, wait_time=0
        ).__aiter__()
        batch: list[Found] = []
        while (raw := await self._call(anext(messages, None))) is not None:
            found = self._found(raw, chat_id)
            if found:
                batch.append(found)
            if len(batch) >= SEARCH_BATCH:
                yield batch
                batch = []
        if batch:
            yield batch

    def _found(self, raw: Any, chat_id: int | None) -> Found | None:
        if chat_id is not None:
            self._remember(chat_id, [raw])
            return Found(convert.message(raw, chat_id))
        chat = convert.chat_from_entity(raw.chat, self._me, self._default_mute)
        return Found(convert.message(raw, chat.id), chat) if chat else None

    # -- live updates --------------------------------------------------------

    def on_event(self, listener: EventListener) -> Unsubscribe:
        self._event_listeners.append(listener)
        return lambda: self._event_listeners.remove(listener)

    def _emit(self, event: Event) -> None:
        for listener in list(self._event_listeners):
            listener(event)

    def _subscribe(self, client: Any) -> None:
        for handler, kind in (
            (self._on_new, events.NewMessage()),
            (self._on_edited, events.MessageEdited()),
            (self._on_deleted, events.MessageDeleted()),
            (self._on_raw, events.Raw()),
        ):
            client.add_event_handler(_logged(handler), kind)

    async def _on_new(self, event: Any) -> None:
        message = await self._from_event(event)
        chat = None
        if event.chat_id not in self._known:
            entity = await event.get_chat()
            chat = convert.chat_from_entity(entity, self._me, self._default_mute)
            self._known.add(event.chat_id)
        self._emit(MessageAdded(message, chat))

    async def _on_edited(self, event: Any) -> None:
        self._emit(MessageChanged(await self._from_event(event)))

    async def _from_event(self, event: Any) -> Message:
        try:
            async with asyncio.timeout(10):
                await event.get_sender()  # may need a request
        except Exception as error:
            log.info("sender unavailable: %s", type(error).__name__)
        self._remember(event.chat_id, [event.message])
        message = convert.message(event.message, event.chat_id)
        if message.reply:
            (message,) = await self._with_replies(event.chat_id, [message])
        return message

    async def _on_deleted(self, event: Any) -> None:
        self._emit(MessagesRemoved(event.chat_id, tuple(event.deleted_ids)))

    async def _on_raw(self, update: Any) -> None:
        match update:
            case types.UpdateReadHistoryInbox():
                chat_id = utils.get_peer_id(update.peer)
                self._emit(InboxRead(chat_id, update.max_id, update.still_unread_count))
            case types.UpdateReadChannelInbox():
                chat_id = utils.get_peer_id(types.PeerChannel(update.channel_id))
                self._emit(InboxRead(chat_id, update.max_id, update.still_unread_count))
            case types.UpdateReadHistoryOutbox():
                self._emit(OutboxRead(utils.get_peer_id(update.peer), update.max_id))
            case types.UpdateReadChannelOutbox():
                chat_id = utils.get_peer_id(types.PeerChannel(update.channel_id))
                self._emit(OutboxRead(chat_id, update.max_id))
            case types.UpdateDialogUnreadMark():
                if isinstance(update.peer, types.DialogPeer):
                    chat_id = utils.get_peer_id(update.peer.peer)
                    self._emit(ChatFlags(chat_id, marked_unread=bool(update.unread)))
            case types.UpdateNotifySettings():
                if isinstance(update.peer, types.NotifyPeer):
                    until = convert.muted_until(update.notify_settings)
                    chat_id = utils.get_peer_id(update.peer.peer)
                    self._emit(ChatFlags(chat_id, muted_until=until))
            case types.UpdateFolderPeers():
                for item in update.folder_peers:
                    chat_id = utils.get_peer_id(item.peer)
                    self._emit(ChatFlags(chat_id, archived=item.folder_id == 1))
            case types.UpdateDialogPinned() | types.UpdatePinnedDialogs():
                self._emit(PinsChanged())
            case (
                types.UpdateDialogFilter()
                | types.UpdateDialogFilters()
                | types.UpdateDialogFilterOrder()
            ):
                self._emit(FoldersChanged())

    # -- signing out ---------------------------------------------------------

    async def log_out(self) -> None:
        client = self._need_client()
        # Telethon would wait forever offline, and deleting the file without
        # revoking would leave a live session that the user cannot see.
        if self.link is not Link.ONLINE:
            raise BackendError(ErrorKind.NETWORK, "You need to be online to log out.")
        if not await self._call(client.log_out()):
            raise BackendError(ErrorKind.OTHER, "Telegram did not confirm the logout.")
        await self._stop_watching()
        self._client = None
        remove_session(self._path)
        self._set_link(Link.OFFLINE)


def _input_media(uploaded: Uploaded) -> Any:
    if uploaded.as_photo:
        return types.InputMediaUploadedPhoto(uploaded.handle)
    path = uploaded.attachment.path
    name = safe_name(path.name)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    attributes: list[Any] = [types.DocumentAttributeFilename(name)]
    if mime == "image/gif":
        attributes.append(types.DocumentAttributeAnimated())
    return types.InputMediaUploadedDocument(
        uploaded.handle, mime, attributes, force_file=mime != "image/gif"
    )


def _best_size(sizes: Iterable[Any], side: int) -> Any:
    """The smallest that is large enough, else the largest."""
    real = sorted(
        (s for s in sizes if isinstance(s, convert.REAL_SIZES)), key=lambda s: max(s.w, s.h)
    )
    return next((s for s in real if max(s.w, s.h) >= side), real[-1] if real else None)


def _logged(handler: Callable[[Any], Awaitable[None]]) -> Callable[[Any], Awaitable[None]]:
    """One bad update must not take down the rest, and must not go unnoticed."""

    async def run(event: Any) -> None:
        try:
            await handler(event)
        except Exception as error:
            log.warning("update not handled", exc_info=error)

    return run


@contextmanager
def _translated() -> Iterator[None]:
    """Turn library exceptions into messages a person can act on."""
    try:
        yield
    except BackendError:
        raise
    except Exception as error:
        translated = translate(error)
        if translated.kind is ErrorKind.OTHER:
            log.warning("unexpected backend error", exc_info=error)
        raise translated from error


def translate(error: Exception) -> BackendError:
    kind = ErrorKind
    match error:
        case SessionUnreadable():
            return BackendError(
                kind.SESSION,
                "The saved login cannot be read. It may belong to another Windows "
                "account, or the file is damaged.",
            )
        case errors.FloodWaitError():
            return BackendError(
                kind.FLOOD, "Too many attempts. Telegram asks you to wait.", error.seconds
            )
        case errors.SlowModeWaitError():
            return BackendError(
                kind.FLOOD, "This group is in slow mode. You have to wait.", error.seconds
            )
        case errors.PhonePasswordFloodError() | errors.PhoneNumberFloodError():
            return BackendError(kind.FLOOD, "Too many attempts. Try again tomorrow.")
        case errors.PhoneNumberInvalidError():
            return BackendError(kind.PHONE, "Telegram does not recognise that phone number.")
        case errors.PhoneNumberUnoccupiedError():
            return BackendError(
                kind.PHONE, "No Telegram account uses that number. Sign up in an official app."
            )
        case errors.PhoneNumberBannedError() | errors.UserDeactivatedBanError():
            return BackendError(kind.ACCOUNT, "Telegram has banned this account.")
        case errors.PhoneCodeExpiredError():
            return BackendError(kind.CODE_EXPIRED, "That code has expired. Request a new one.")
        case errors.PhoneCodeInvalidError() | errors.PhoneCodeEmptyError():
            return BackendError(kind.CODE, "That code is not right.")
        case errors.PasswordHashInvalidError():
            return BackendError(kind.PASSWORD, "That password is not right.")
        case errors.SendCodeUnavailableError():
            return BackendError(kind.CODE, "Telegram has no other way to send the code.")
        case errors.SessionPasswordNeededError():
            return BackendError(kind.PASSWORD, "This account has a password.")
        case errors.ApiIdInvalidError() | errors.ApiIdPublishedFloodError():
            return BackendError(kind.API_KEYS, "Telegram rejected the API ID or API hash.")
        case errors.UnauthorizedError() | errors.AuthKeyError():
            return BackendError(kind.SESSION, "Telegram signed this session out.")
        case errors.RPCError() if type(error) in _REFUSALS:
            return BackendError(kind.REFUSED, _REFUSALS[type(error)])
        case errors.RPCError() if error.message in _REFUSAL_CODES:
            return BackendError(kind.REFUSED, _REFUSAL_CODES[error.message])
        case errors.RPCError():
            return BackendError(kind.OTHER, f"Telegram reported an error ({error.message}).")
        case TimeoutError() | ConnectionError() | OSError() | asyncio.IncompleteReadError():
            return BackendError(kind.NETWORK, _OFFLINE)
        case _ if type(error).__module__.startswith("python_socks"):
            return BackendError(kind.NETWORK, _OFFLINE)
        case _:
            return BackendError(kind.OTHER, f"Unexpected problem ({type(error).__name__}).")
