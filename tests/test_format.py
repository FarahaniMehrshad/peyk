from datetime import UTC, datetime

import pytest

from peyk.models import Chat, Kind, Message, Span
from peyk.text.format import emoji, notice, snippet, typing


@pytest.mark.parametrize(
    ("text", "query", "expected"),
    [
        ("short text", "text", "short text"),
        ("x" * 50 + " needle here", "needle", "…" + "x" * 19 + " needle here"),
        ("x" * 50 + " Needle here", "NEEDLE", "…" + "x" * 19 + " Needle here"),
        ("x" * 50 + " one", "one two", "…" + "x" * 19 + " one"),
        ("y" * 80, "absent", "y" * 80),
        ("y" * 80, "", "y" * 80),
    ],
)
def test_snippet_starts_near_what_was_found(text, query, expected):
    message = Message(1, 1, datetime(2026, 1, 1, tzinfo=UTC), text)
    assert snippet(message, query) == expected


@pytest.mark.parametrize(("typists", "person", "pieces"), [
    ([], "", []),
    ([("Reza", "typing")], "", ["Reza", " is typing…"]),
    ([("", "typing")], "", ["Someone", " is typing…"]),
    ([("", "sending a file")], "Sara", ["Sara", " is sending a file…"]),
    ([("Reza", "typing"), ("Mina", "sending a photo")], "",
     ["Reza", " and ", "Mina", " are typing…"]),
    ([("Reza", "typing"), ("", "typing")], "", ["2 people are typing…"]),
    ([("a", "typing")] * 5, "", ["5 people are typing…"]),
])  # fmt: skip
def test_who_is_typing(typists, person, pieces):
    assert typing(typists, person) == pieces


@pytest.mark.parametrize(("sent", "shown"), [
    ("\u2764", "\u2764\ufe0f"), ("\u2764\u200d\U0001f525", "\u2764\ufe0f\u200d\U0001f525"),
    ("\U0001f44d", "\U0001f44d"), ("\u2764\ufe0f", "\u2764\ufe0f"), ("\u26a1", "\u26a1"),
    ("a", "a"), ("", ""),
])  # fmt: skip
def test_emoji_are_drawn_as_pictures(sent, shown):
    assert emoji(sent) == shown


def test_notices():
    chat = Chat(-5, "Team", Kind.GROUP)
    date = datetime(2026, 11, 22, tzinfo=UTC)
    message = Message(1, -5, date, "tell nobody:\nthe end", 7, "Reza Karimi",
                      spans=(Span(13, 99, "spoiler"),))  # fmt: skip
    assert notice(message, chat) == "Reza: tell nobody: ▒▒▒ ▒▒▒"
