"""Telethon objects to Peyk models. Pure functions; every string is sanitized here."""

from __future__ import annotations

from collections.abc import Iterable
from copy import copy
from dataclasses import replace
from datetime import datetime
from typing import Any

from telethon import types, utils

from peyk.files import safe_name
from peyk.models import FOREVER, Chat, Folder, Kind, Media, Message, Reply, Span
from peyk.text import format as fmt
from peyk.text.markup import utf16_length
from peyk.text.sanitize import sanitize_line, sanitize_map

_SPANS: dict[type, str] = {
    types.MessageEntityBold: "bold",
    types.MessageEntityItalic: "italic",
    types.MessageEntityUnderline: "underline",
    types.MessageEntityStrike: "strike",
    types.MessageEntityCode: "code",
    types.MessageEntityPre: "pre",
    types.MessageEntitySpoiler: "spoiler",
    types.MessageEntityBlockquote: "quote",
    types.MessageEntityUrl: "link",
    types.MessageEntityTextUrl: "link",
    types.MessageEntityEmail: "link",
    types.MessageEntityMention: "mention",
    types.MessageEntityMentionName: "mention",
    types.MessageEntityHashtag: "tag",
    types.MessageEntityCashtag: "tag",
    types.MessageEntityBotCommand: "tag",
}
_SENT: dict[str, type] = {
    "bold": types.MessageEntityBold,
    "italic": types.MessageEntityItalic,
    "strike": types.MessageEntityStrike,
    "spoiler": types.MessageEntitySpoiler,
    "code": types.MessageEntityCode,
}


# The server adds these itself, from the text.
_AUTOMATIC = (
    types.MessageEntityUrl,
    types.MessageEntityEmail,
    types.MessageEntityPhone,
    types.MessageEntityMention,
    types.MessageEntityHashtag,
    types.MessageEntityCashtag,
    types.MessageEntityBotCommand,
    types.MessageEntityBankCard,
)
_TELEGRAM = 777000  # its service notifications
REAL_SIZES = (types.PhotoSize, types.PhotoSizeProgressive, types.PhotoCachedSize)
PICTURE_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_PICTURE = 20_000_000  # bytes; larger image files are only downloaded on request


def plain(value: Any) -> str:
    """Text that newer API layers wrap in TextWithEntities."""
    return getattr(value, "text", value) or ""


def name_of(entity: Any) -> str:
    if entity is None:
        return ""
    if getattr(entity, "deleted", False):
        return "Deleted account"
    return sanitize_line(utils.get_display_name(entity) or "")


def kind_of(entity: Any, me: int) -> Kind | None:
    if isinstance(entity, types.User):
        if entity.id == me or entity.is_self:
            return Kind.SAVED
        return Kind.BOT if entity.bot else Kind.USER
    if isinstance(entity, types.Channel):
        return Kind.CHANNEL if entity.broadcast else Kind.GROUP
    if isinstance(entity, types.ChannelForbidden):
        return Kind.CHANNEL if entity.broadcast else Kind.GROUP
    if isinstance(entity, (types.Chat, types.ChatForbidden, types.ChatEmpty)):
        return Kind.GROUP
    return None


def muted_until(settings: Any, default: float = 0.0) -> float:
    """Telethon turns both "unset" and "explicitly unmuted" into None."""
    until = getattr(settings, "mute_until", None)
    if until is None:
        return default
    if isinstance(until, datetime):
        until = until.timestamp()
    return FOREVER if until >= 2**31 - 86400 * 366 else float(until)


def chat_from_entity(entity: Any, me: int, default_mute: dict[Kind, float]) -> Chat | None:
    kind = kind_of(entity, me)
    if kind is None:
        return None
    title = "Saved Messages" if kind is Kind.SAVED else name_of(entity) or "Unnamed chat"
    rights = getattr(entity, "admin_rights", None)
    return Chat(
        id=utils.get_peer_id(entity),
        title=title,
        kind=kind,
        muted_until=default_mute.get(kind, 0.0),
        contact=bool(getattr(entity, "contact", False)),
        username=username_of(entity),
        can_write=can_write(entity, kind),
        can_delete_any=bool(
            getattr(entity, "creator", False) or getattr(rights, "delete_messages", False)
        ),
    )


def username_of(entity: Any) -> str:
    name = getattr(entity, "username", None)
    if not name:
        extra = [u for u in getattr(entity, "usernames", None) or () if u.active]
        name = extra[0].username if extra else ""
    return sanitize_line(name or "")


def can_write(entity: Any, kind: Kind) -> bool:
    """What we can tell beforehand; Telegram has the last word."""
    if isinstance(entity, types.User):
        return not entity.deleted and entity.id != _TELEGRAM
    if isinstance(entity, (types.ChatForbidden, types.ChannelForbidden, types.ChatEmpty)):
        return False
    rights = getattr(entity, "admin_rights", None)
    if getattr(entity, "creator", False):
        return True
    if kind is Kind.CHANNEL:
        return bool(getattr(rights, "post_messages", False))
    if isinstance(entity, types.Chat) and (entity.left or entity.deactivated):
        return False
    if rights:
        return True
    limits = (getattr(entity, "banned_rights", None), entity.default_banned_rights)
    return not any(limit and (limit.send_messages or limit.send_plain) for limit in limits)


def chat_from_dialog(
    dialog: Any, me: int, default_mute: dict[Kind, float], pin: int
) -> Chat | None:
    base = chat_from_entity(dialog.entity, me, default_mute)
    if base is None:
        return None
    raw = dialog.dialog
    return Chat(
        id=base.id,
        title=base.title,
        kind=base.kind,
        last=message(dialog.message, base.id) if dialog.message else None,
        unread=raw.unread_count or 0,
        mentions=raw.unread_mentions_count or 0,
        marked_unread=bool(raw.unread_mark),
        muted_until=muted_until(raw.notify_settings, base.muted_until),
        pin=pin,
        archived=raw.folder_id == 1,
        contact=base.contact,
        read_inbox=raw.read_inbox_max_id or 0,
        read_outbox=raw.read_outbox_max_id or 0,
        username=base.username,
        can_write=base.can_write,
        can_delete_any=base.can_delete_any,
    )


def folder(raw: Any, me: int) -> Folder | None:
    def ids(peers: Any) -> list[int]:
        found = []
        for peer in peers or ():
            if isinstance(peer, types.InputPeerSelf):
                found.append(me)
                continue
            try:
                found.append(utils.get_peer_id(peer))
            except TypeError:
                continue
        return found

    if isinstance(raw, types.DialogFilterDefault) or not hasattr(raw, "include_peers"):
        return None
    flags = {
        "contacts": "contacts",
        "non_contacts": "non_contacts",
        "groups": "groups",
        "channels": "broadcasts",
        "bots": "bots",
        "exclude_muted": "exclude_muted",
        "exclude_read": "exclude_read",
        "exclude_archived": "exclude_archived",
    }
    return Folder(
        id=raw.id,
        title=sanitize_line(plain(raw.title)) or f"Folder {raw.id}",
        pinned=tuple(ids(raw.pinned_peers)),
        include=frozenset(ids(raw.include_peers)),
        exclude=frozenset(ids(getattr(raw, "exclude_peers", None))),
        **{ours: bool(getattr(raw, theirs, False)) for ours, theirs in flags.items()},
    )


# -- messages ---------------------------------------------------------------


def text_and_spans(text: str, entities: Any) -> tuple[str, tuple[Span, ...]]:
    """Telegram counts UTF-16 units; we count characters of the sanitized text."""
    clean, where = sanitize_map(text)
    if not entities:
        return clean, ()
    # units[n] is the index of the character that UTF-16 unit n belongs to.
    units: list[int] = []
    for i, char in enumerate(text):
        units.extend([i] * (2 if ord(char) > 0xFFFF else 1))
    units.append(len(text))
    spans = []
    for entity in entities:
        kind = _SPANS.get(type(entity))
        if kind is None:
            continue
        last = len(units) - 1
        start = where[units[min(max(entity.offset, 0), last)]]
        end = where[units[min(max(entity.offset + entity.length, 0), last)]]
        if start < end:
            url = sanitize_line(getattr(entity, "url", "") or "")
            spans.append(Span(start, end, kind, url))
    return clean, tuple(spans)


def entities(text: str, spans: Iterable[Span]) -> list[Any]:
    """Formatting we send, in Telegram's units."""
    made: list[Any] = []
    for span in spans:
        start = utf16_length(text[: span.start])
        length = utf16_length(text[span.start : span.end])
        match span.kind:
            case "link":
                made.append(types.MessageEntityTextUrl(start, length, span.url))
            case "pre":
                made.append(types.MessageEntityPre(start, length, span.url))
            case _:
                made.append(_SENT[span.kind](start, length))
    return made


def own_ranges(text: str, found: Any) -> tuple[list[Any], list[tuple[int, int]]]:
    """Formatting worth keeping in an edit, and which characters each covers."""
    kept = [e for e in found or () if not isinstance(e, _AUTOMATIC)]
    units = [i for i, char in enumerate(text) for _ in range(2 if ord(char) > 0xFFFF else 1)]
    units.append(len(text))
    last = len(units) - 1
    ranges = [
        (units[min(max(e.offset, 0), last)], units[min(max(e.offset + e.length, 0), last)])
        for e in kept
    ]
    return kept, ranges


def moved(entity: Any, text: str, place: tuple[int, int]) -> Any:
    """The same formatting over other characters."""
    entity = copy(entity)
    entity.offset = utf16_length(text[: place[0]])
    entity.length = utf16_length(text[place[0] : place[1]])
    return entity


def media(m: Any) -> Media | None:
    raw = getattr(m, "media", None)
    if raw is None or isinstance(raw, types.MessageMediaWebPage):
        return None
    found = _labelled(m, raw)
    return replace(found, **_file_details(m, raw)) if found else None


def _file_details(m: Any, raw: Any) -> dict[str, Any]:
    file, thing = m.file, m.photo or m.document
    if not file or not getattr(thing, "id", 0):
        return {}
    if getattr(raw, "ttl_seconds", None):
        return {"locked": "It can be seen only once, in an official app."}
    details: dict[str, Any] = {
        "id": thing.id,
        "name": sanitize_line(file.name or ""),
        "mime": sanitize_line(file.mime_type or ""),
        "size": file.size or 0,
        "spoiler": bool(getattr(raw, "spoiler", False)),
    }
    if getattr(m, "noforwards", False) or getattr(m.chat, "noforwards", False):
        details["locked"] = "This chat does not allow saving files."
    sizes = (thing.sizes if m.photo else thing.thumbs) or ()
    for size in sizes:
        if isinstance(size, types.PhotoStrippedSize):
            details["preview"] = utils.stripped_photo_to_jpg(size.bytes)
    real = [s for s in sizes if isinstance(s, REAL_SIZES)]
    own = (file.width or 0, file.height or 0)
    if real:
        largest = max(real, key=lambda s: s.w * s.h)
        # Thumbnails can be cut square; the file's own proportions are the right ones.
        shape = own if min(own) > 0 else (largest.w, largest.h)
    elif m.document and file.mime_type in PICTURE_TYPES and details["size"] <= MAX_PICTURE:
        shape = own
    else:
        return details
    if min(shape) > 0:
        details |= {"picture": True, "width": shape[0], "height": shape[1]}
    return details


def _labelled(m: Any, raw: Any) -> Media | None:
    file = m.file
    length = fmt.duration(file.duration) if file and file.duration else ""
    if m.photo:
        dims = f" {file.width}x{file.height}" if file and file.width else ""
        return Media("photo", f"Photo{dims}")
    if m.sticker:
        return Media("sticker", f"Sticker {sanitize_line(file.emoji or '')}".strip())
    if m.gif:
        return Media("gif", "GIF")
    if m.video_note:
        return Media("video_note", f"Video message {length}".strip())
    if m.video:
        return Media("video", f"Video {length}".strip())
    if m.voice:
        return Media("voice", f"Voice message {length}".strip())
    if m.audio:
        title = " - ".join(p for p in (file.performer, file.title) if p) or file.name or ""
        return Media("audio", f"Audio {sanitize_line(title)} {length}".strip())
    if m.document:
        name = safe_name(file.name or "")
        weight = f" ({fmt.size(file.size)})" if file and file.size else ""
        return Media("document", f"File {name}{weight}")
    if m.contact:
        who = " ".join(p for p in (m.contact.first_name, m.contact.last_name) if p)
        return Media("contact", f"Contact {sanitize_line(who)}".strip())
    if m.venue:
        return Media("venue", f"Place {sanitize_line(m.venue.title or '')}".strip())
    if m.geo:
        live = isinstance(raw, types.MessageMediaGeoLive)
        return Media("location", "Live location" if live else "Location")
    if m.poll:
        poll = m.poll.poll
        label = "Quiz" if poll.quiz else "Poll"
        return Media("poll", f"{label}: {sanitize_line(plain(poll.question))}")
    if m.dice:
        return Media("dice", f"{sanitize_line(m.dice.emoticon or '')} {m.dice.value}".strip())
    if m.game:
        return Media("game", f"Game {sanitize_line(m.game.title or '')}".strip())
    if m.invoice:
        return Media("invoice", f"Invoice {sanitize_line(m.invoice.title or '')}".strip())
    labels = {
        "MessageMediaStory": "Story",
        "MessageMediaToDo": "Checklist",
        "MessageMediaGiveaway": "Giveaway",
        "MessageMediaGiveawayResults": "Giveaway results",
        "MessageMediaPaidMedia": "Paid media",
        "MessageMediaPhoto": "Photo (expired)",
        "MessageMediaDocument": "File (expired)",
    }
    name = type(raw).__name__
    if name == "MessageMediaEmpty":
        return None
    return Media("other", labels.get(name, "Unsupported content"))


def poll_options(m: Any) -> str:
    if not getattr(m, "poll", None):
        return ""
    return "\n".join(f"- {sanitize_line(plain(a.text))}" for a in m.poll.poll.answers)


def service_text(m: Any, who: str) -> str:
    action = m.action
    people = [name_of(e) for e in (m.action_entities or []) if e is not None]
    others = ", ".join(p for p in people if p) or "someone"
    title = sanitize_line(str(getattr(action, "title", "") or ""))
    match type(action).__name__.removeprefix("MessageAction"):
        case "ChatAddUser":
            return f"{who} joined" if others == who else f"{who} added {others}"
        case "ChatDeleteUser":
            return f"{who} left" if others == who else f"{who} removed {others}"
        case "ChatJoinedByLink" | "ChatJoinedByRequest" | "ChatJoinedViaCommunity":
            return f"{who} joined"
        case "ChatCreate":
            return f"{who} created the group {title}".strip()
        case "ChannelCreate":
            return f"{title} was created".strip()
        case "ChatEditTitle":
            return f"{who} renamed the chat to {title}"
        case "ChatEditPhoto":
            return f"{who} changed the chat photo"
        case "ChatDeletePhoto":
            return f"{who} removed the chat photo"
        case "PinMessage":
            return f"{who} pinned a message"
        case "HistoryClear":
            return "History was cleared"
        case "ContactSignUp":
            return f"{who} joined Telegram"
        case "ScreenshotTaken":
            return f"{who} took a screenshot"
        case "PhoneCall":
            call = "Video call" if action.video else "Call"
            missed = isinstance(action.reason, types.PhoneCallDiscardReasonMissed)
            if missed:
                return f"Missed {call.lower()}" if not m.out else f"{call}, not answered"
            length = f", {fmt.duration(action.duration)}" if action.duration else ""
            return f"{'Outgoing' if m.out else 'Incoming'} {call.lower()}{length}"
        case "GroupCall":
            return "Voice chat ended" if action.duration else "Voice chat started"
        case "ChatMigrateTo" | "ChannelMigrateFrom":
            return "The group was upgraded"
        case "SetMessagesTTL":
            return f"{who} changed the auto-delete timer"
        case "TopicCreate":
            return f"{who} created the topic {title}"
        case "CustomAction":
            return sanitize_line(action.message or "") or "Service message"
        case _:
            return "Service message"


def reply_id(m: Any) -> int:
    header = getattr(m, "reply_to", None)
    if not isinstance(header, types.MessageReplyHeader) or not header.reply_to_msg_id:
        return 0
    if header.reply_to_peer_id is not None:
        return 0  # a reply to another chat
    if header.forum_topic and not header.reply_to_top_id:
        return 0  # merely belongs to a topic
    return header.reply_to_msg_id


def _forwarded(m: Any) -> str:
    forward = m.forward
    if not forward:
        return ""
    name = name_of(forward.sender) or name_of(forward.chat)
    return name or sanitize_line(forward.from_name or "") or "unknown"


def message(m: Any, chat_id: int, reply: Reply | None = None) -> Message:
    sender = name_of(m.sender)
    if not sender:
        sender = sanitize_line(m.post_author or "") or name_of(m.chat) or "Unknown"
    common = {
        "id": m.id,
        "chat_id": chat_id,
        "date": m.date,
        "sender_id": m.sender_id or 0,
        "sender": sender,
        "outgoing": bool(m.out),
        "mentioned": bool(m.mentioned),
    }
    if getattr(m, "action", None) is not None:
        return Message(text=service_text(m, sender), service=True, **common)
    text, spans = text_and_spans(m.message or "", m.entities)
    options = poll_options(m)
    if options:
        text = f"{text}\n{options}".lstrip("\n")
    target = reply_id(m)
    if reply is None and target:
        quote = sanitize_line(getattr(m.reply_to, "quote_text", None) or "")
        reply = Reply(target, "", quote)
    return Message(
        text=text,
        spans=spans,
        media=media(m),
        reply=reply,
        forwarded=_forwarded(m),
        edited=bool(m.edit_date) and not m.edit_hide,
        **common,
    )


def reply_to(original: Message, quote: str = "") -> Reply:
    name = "You" if original.outgoing else original.sender
    return Reply(original.id, name, quote or fmt.body(original))
