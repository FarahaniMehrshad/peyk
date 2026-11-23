"""Dates, previews and other short strings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta

from rich.cells import cell_len

from peyk.models import Chat, Kind, Message

MAX_NOTICE = 200
KINDS = {
    Kind.USER: "person",
    Kind.BOT: "bot",
    Kind.GROUP: "group",
    Kind.CHANNEL: "channel",
    Kind.SAVED: "notes",
}


def clock(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M")


def list_date(moment: datetime, now: datetime | None = None) -> str:
    """Short date for the chat list: today's time, weekday, or day and month."""
    local = moment.astimezone()
    today = (now or datetime.now()).astimezone().date()
    age = (today - local.date()).days
    if age <= 0:
        return local.strftime("%H:%M")
    if age < 7:
        return local.strftime("%a")
    if local.year == today.year:
        return f"{local.day} {local.strftime('%b')}"
    return local.strftime("%d.%m.%y")


def day_heading(day: date, today: date | None = None) -> str:
    today = today or datetime.now().date()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    heading = f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"
    return heading if day.year == today.year else f"{heading} {day.year}"


def body(message: Message) -> str:
    """The message as one line, without who sent it."""
    parts = [f"[{message.media.label}]" if message.media else "", message.text]
    return " ".join(" ".join(part.split()) for part in parts if part)


def snippet(message: Message, query: str) -> str:
    """The message as one line, starting shortly before what was searched for."""
    text = body(message)
    words = query.casefold().split()
    lowered = text.casefold()
    found = lowered.find(query.casefold().strip())
    if found < 0 and words:
        found = lowered.find(words[0])
    return "…" + text[found - 20 :].lstrip() if found > 30 else text


def preview(message: Message, chat: Chat) -> str:
    text = body(message)
    if message.service or chat.kind in (Kind.SAVED, Kind.CHANNEL):
        return text
    if message.outgoing:
        return f"You: {text}"
    if chat.kind is Kind.GROUP and message.sender:
        return f"{message.sender.split()[0]}: {text}"
    return text


def notice(message: Message, chat: Chat) -> str:
    """What a notification says: short, with spoilers covered."""
    text = list(message.text)
    for span in message.spans:
        if span.kind == "spoiler":
            for i in range(span.start, min(span.end, len(text))):
                if not text[i].isspace():
                    text[i] = "▒"
    line = preview(replace(message, text="".join(text)), chat)
    return line if len(line) <= MAX_NOTICE else line[: MAX_NOTICE - 1].rstrip() + "…"


def typing(typists: Sequence[tuple[str, str]], person: str = "") -> list[str]:
    """(name, what they do) of everyone typing, in pieces to be drawn one by one.

    Names may be right-to-left, so they must not be mixed with the rest. With
    `person`, the chat is with them alone.
    """
    if not typists:
        return []
    if person or len(typists) == 1:
        name, what = typists[0]
        return [person or name or "Someone", f" is {what}…"]
    names = [name for name, _ in typists if name]
    if len(typists) == 2 and len(names) == 2:
        return [names[0], " and ", names[1], " are typing…"]
    return [f"{len(typists)} people are typing…"]


def emoji(text: str) -> str:
    """Drawn as a picture, not as a black and white sign half as wide."""
    if len(text) and ord(text[0]) > 0x2000 and cell_len(text) == 1 and "\ufe0f" not in text:
        return f"{text[0]}\ufe0f{text[1:]}"
    return text


def count(number: int) -> str:
    if number < 1000:
        return str(number)
    return f"{number // 1000}K" if number < 1_000_000 else f"{number // 1_000_000}M"


def size(number: int) -> str:
    value = float(number)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" or value >= 10 else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError


def duration(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"
