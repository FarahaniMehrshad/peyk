from datetime import UTC, datetime

import pytest

from peyk.models import Message
from peyk.text.format import snippet


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
