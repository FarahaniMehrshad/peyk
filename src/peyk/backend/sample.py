"""Made-up chats for --demo and tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from peyk.backend import sample_files
from peyk.models import (
    FOREVER,
    Chat,
    Folder,
    Kind,
    Media,
    Message,
    Reaction,
    ReactionChoice,
    Reply,
    Span,
)
from peyk.text.format import emoji

ME = 1
SARA, BOT, GROUP, NEWS, LONG, HEBREW, MUTED, OLD = 10, 11, -20, -1_000_000_000_030, -21, 12, 13, 14
BIG = -1_000_000_000_040
PHOTOS = -22
_REACTIONS = (
    ("❤", "Red Heart"), ("👍", "Thumbs Up"), ("🔥", "Fire"), ("😁", "Beaming Face"),
    ("🎉", "Party Popper"), ("👏", "Clapping Hands"), ("😢", "Crying Face"),
    ("🤔", "Thinking Face"), ("🙏", "Folded Hands"), ("👎", "Thumbs Down"), ("🥰", "Loving Face"),
    ("🤯", "Exploding Head"), ("😱", "Screaming Face"), ("🤬", "Cursing Face"),
    ("🤩", "Star-Struck"), ("💩", "Pile of Poo"), ("👌", "Ok Hand"), ("🕊", "Dove"),
    ("🤡", "Clown Face"), ("🥱", "Yawning Face"), ("🥴", "Woozy Face"), ("😍", "Heart Eyes"),
    ("🐳", "Whale"), ("❤\u200d🔥", "Heart on Fire"), ("🌚", "New Moon"), ("🌭", "Hot Dog"),
    ("💯", "Hundred Points"), ("🤣", "Rolling on the Floor"), ("⚡", "High Voltage"),
    ("🍌", "Banana"), ("🏆", "Trophy"), ("💔", "Broken Heart"), ("🤨", "Raised Eyebrow"),
    ("😐", "Neutral Face"), ("🍓", "Strawberry"), ("🍾", "Champagne"), ("💋", "Kiss Mark"),
    ("😈", "Smiling Devil"), ("😴", "Sleeping Face"), ("😭", "Loudly Crying"),
    ("🤓", "Nerd Face"), ("👻", "Ghost"), ("👨\u200d💻", "Technologist"), ("👀", "Eyes"),
    ("🎃", "Pumpkin"), ("🙈", "See-No-Evil"), ("😇", "Halo"), ("🤝", "Handshake"),
    ("✍", "Writing Hand"), ("🤗", "Hugging Face"), ("☃", "Snowman"), ("💅", "Nail Polish"),
    ("🗿", "Moai"), ("🆒", "Cool"), ("🦄", "Unicorn"), ("💊", "Pill"),
    ("🤷\u200d♂", "Man Shrugging"), ("😡", "Pouting Face"),
)  # fmt: skip
REACTIONS = tuple(ReactionChoice(key, emoji(key), name) for key, name in _REACTIONS)


def reacted(*given: tuple[str, int, bool]) -> tuple[Reaction, ...]:
    return tuple(Reaction(key, emoji(key), count, mine) for key, count, mine in given)


_LOCKED = "This chat does not allow saving files."


class _Writer:
    """Builds one chat's messages, a few minutes apart, ending near `end`."""

    def __init__(self, chat_id: int, end: datetime, step: timedelta, first_id: int = 100) -> None:
        self.chat_id = chat_id
        self.end = end
        self.step = step
        self.next_id = first_id
        self.messages: list[Message] = []

    def say(self, sender: tuple[int, str], text: str, **extra) -> Message:
        message = Message(
            id=self.next_id,
            chat_id=self.chat_id,
            date=self.end,
            text=text,
            sender_id=sender[0],
            sender=sender[1],
            outgoing=sender[0] == ME,
            **extra,
        )
        self.next_id += 1
        self.messages.append(message)
        return message

    def finish(self) -> list[Message]:
        total = len(self.messages)
        return [
            Message(**{**_fields(m), "date": self.end - self.step * (total - 1 - i)})
            for i, m in enumerate(self.messages)
        ]


def _fields(message: Message) -> dict:
    return {name: getattr(message, name) for name in Message.__dataclass_fields__}


def _span(text: str, part: str, kind: str, url: str = "") -> Span:
    start = text.index(part)
    return Span(start, start + len(part), kind, url)


def _photo(media_id: int, width: int, height: int, **extra) -> Media:
    media = Media("photo", f"Photo {width}x{height}", media_id, "", "image/jpeg", 90_000,
                  True, width, height, **extra)  # fmt: skip
    return replace(media, preview=sample_files.tiny(media))


def build(now: datetime | None = None) -> tuple[list[Chat], dict[int, list[Message]], list[Folder]]:
    now = (now or datetime.now()).astimezone()
    me, sara, reza, ali, mina = (
        (ME, "You"), (SARA, "سارا محمدی"), (50, "Reza"), (51, "علی"), (52, "Mina K."),
    )  # fmt: skip
    minute = timedelta(minutes=1)

    w = _Writer(SARA, now - 3 * minute, 2 * minute)
    w.say(sara, "سلام! خوبی؟")
    w.say(me, "سلام سارا، ممنون. تو چطوری؟")
    w.say(sara, "خوبم. می\u200cخواهم فردا کتاب\u200cها را بیاورم، ساعت ۱۲:۳۰ خوب است؟")
    question = w.messages[-1]
    w.say(me, "عالیه 👍🏽 لطفا کتاب Python را هم بیاور (نسخهٔ 2026).",
          reply=Reply(question.id, sara[1], question.text))  # fmt: skip
    w.say(sara, "", media=_photo(1, 1280, 960), reactions=reacted(("❤", 1, True)))
    link = "این را ببین: https://example.com/books و @mina هم می\u200cآید"
    w.say(sara, link, spans=(_span(link, "https://example.com/books", "link"),
                             _span(link, "@mina", "mention")))  # fmt: skip
    w.say(me, "مرسی! فردا می\u200cبینمت ❤️")
    w.say(sara, "باشه، خداحافظ")
    sara_messages = w.finish()

    w = _Writer(GROUP, now - minute, 3 * minute)
    w.say(reza, "created the group", service=True)
    w.say(reza, "Hey everyone, the build is green again.",
          reactions=reacted(("🎉", 3, False), ("👍", 2, True), ("❤\u200d🔥", 1, False)))
    code = "Run this first:\nuv sync\nuv run pytest"
    w.say(reza, code, spans=(_span(code, "uv sync\nuv run pytest", "pre"),))
    styled = "This is bold, italic, struck, code and a secret."
    w.say(mina, styled, spans=(
        _span(styled, "bold", "bold"), _span(styled, "italic", "italic"),
        _span(styled, "struck", "strike"), _span(styled, "code", "code"),
        _span(styled, "a secret", "spoiler"),
    ), edited=True)  # fmt: skip
    w.say(ali, "سلام بچه\u200cها، جلسهٔ امروز ساعت ۱۷ است. لطفا همه باشید!",
          reactions=reacted(("👌", 2, False)))
    w.say(me, "I'll be there.")
    w.say(me, "Can someone share the notes?")
    w.say(mina, "Sure", reply=Reply(w.messages[-1].id, "You", "Can someone share the notes?"))
    w.say(mina, "", media=Media("document", "File notes-2026.txt (2.4 KB)", 2, "notes-2026.txt",
                                "text/plain", 2400))  # fmt: skip
    w.say(reza, "Release plan", forwarded="Peyk News")
    w.say(ali, "joined", service=True)
    w.say(ali, "دیر رسیدم، ببخشید 🙏")
    group_messages = w.finish()

    w = _Writer(NEWS, now - 40 * minute, 60 * minute)
    news = (0, "Peyk News")
    for i in range(1, 26):
        w.say(news, f"Update {i}: the terminal is a fine place to read.",
              reactions=reacted(("👍", 1200 + i, False), ("🔥", 48, i == 25), ("🤔", 7, False)))
    w.say(news, "نسخهٔ تازه منتشر شد. جزئیات بیشتر به\u200cزودی اعلام می\u200cشود.")
    news_messages = w.finish()

    w = _Writer(LONG, now - 5 * 60 * minute, 7 * minute)
    for i in range(1, 321):
        who = (reza, mina, ali, me)[i * 7 % 4]
        if i % 5 == 0:
            text = f"پیام شمارهٔ {i}: این یک متن نسبتا طولانی است که باید در چند سطر شکسته شود " * 2
        else:
            text = f"Message {i}. " + "Some filler text to wrap around. " * (i % 4)
        w.say(who, text.strip())
    long_messages = w.finish()

    w = _Writer(BIG, now - 26 * 60 * minute, 4 * minute)
    for i in range(1, 181):
        w.say((reza, mina)[i % 2], f"Channel-style group message {i}")
    big_messages = w.finish()

    w = _Writer(PHOTOS, now - 30 * 60 * minute, 5 * minute)
    park = (PHOTOS, "Photo Club")
    w.say(reza, "Pictures from the weekend:")
    w.say(reza, "", media=_photo(21, 1600, 900))
    w.say(mina, "یک عکس بلند از کوه", media=_photo(22, 720, 1280))
    w.say(mina, "", media=_photo(23, 1000, 1000, spoiler=True))
    w.say(ali, "", media=Media("sticker", "Sticker 😀", 24, "sticker.webp", "image/webp", 20_000,
                               True, 512, 512))  # fmt: skip
    w.say(ali, "", media=Media("video", "Video 0:12", 25, "", "video/mp4", 3_000_000,
                               True, 1280, 720))  # fmt: skip
    w.say(reza, "Run me!", media=Media("document", "File setup.exe (12 KB)", 26, "setup.exe",
                                       "application/octet-stream", 12_000))  # fmt: skip
    w.say(reza, "", media=Media("photo", "Photo 800x600", 27, "", "image/jpeg", 90_000, True,
                                800, 600, locked=_LOCKED))  # fmt: skip
    links = "Two links: https://example.com/a and the docs"
    hidden = _span(links, "the docs", "link", "https://example.org/docs")
    w.say(park, links, spans=(_span(links, "https://example.com/a", "link"), hidden))
    photo_messages = w.finish()

    def small(chat_id: int, name: str, age: timedelta, *texts: str) -> list[Message]:
        writer = _Writer(chat_id, now - age, minute)
        for text in texts:
            writer.say((chat_id, name), text)
        return writer.finish()

    histories = {
        SARA: sara_messages,
        GROUP: group_messages,
        NEWS: news_messages,
        LONG: long_messages,
        BIG: big_messages,
        HEBREW: small(HEBREW, "דנה", timedelta(hours=3), "שלום! מה שלומך? (test)"),
        BOT: small(BOT, "Weather Bot", timedelta(hours=8), "Sunny, 24 °C ☀️"),
        PHOTOS: photo_messages,
        MUTED: small(MUTED, "Noisy Nick", timedelta(days=2), "hello?", "anyone?", "HELLO"),
        OLD: small(OLD, "Old Friend", timedelta(days=400), "Long time no see"),
        ME: small(ME, "You", timedelta(days=1), "Remember: buy milk"),
    }

    def chat(chat_id: int, title: str, kind: Kind, unread: int = 0, **extra) -> Chat:
        messages = histories[chat_id]
        seen = messages[-1 - unread].id if unread < len(messages) else 0
        return Chat(id=chat_id, title=title, kind=kind, last=messages[-1], unread=unread,
                    read_inbox=seen, read_outbox=messages[-1].id, **extra)  # fmt: skip

    chats = [
        chat(SARA, "سارا محمدی", Kind.USER, unread=1, pin=1, contact=True),
        chat(GROUP, "Peyk Developers", Kind.GROUP, unread=3, mentions=1),
        chat(NEWS, "Peyk News", Kind.CHANNEL, unread=12),
        chat(HEBREW, "דנה", Kind.USER, contact=True),
        chat(LONG, "گروه بزرگ با یک نام بسیار طولانی که جا نمی\u200cشود", Kind.GROUP),
        chat(BOT, "Weather Bot", Kind.BOT),
        chat(ME, "Saved Messages", Kind.SAVED),
        chat(BIG, "Big Supergroup", Kind.GROUP, unread=30),
        chat(PHOTOS, "Photo Club", Kind.GROUP),
        chat(MUTED, "Noisy Nick", Kind.USER, unread=3, muted_until=FOREVER),
        chat(OLD, "Old Friend", Kind.USER, archived=True, contact=True),
    ]
    folders = [
        Folder(2, "Personal", contacts=True, non_contacts=True, exclude_archived=True),
        Folder(3, "گروه\u200cها", groups=True),
        Folder(4, "Unread", contacts=True, non_contacts=True, groups=True, channels=True,
               bots=True, exclude_read=True, exclude_muted=True),
        Folder(5, "Empty"),
    ]  # fmt: skip
    return chats, histories, folders
