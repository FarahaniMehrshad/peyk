"""Make untrusted text safe to write to a terminal."""

from __future__ import annotations

from unicodedata import category


def sanitize_map(text: str) -> tuple[str, list[int]]:
    """Sanitized text, and for each index of `text` (and its end) where it went."""
    out: list[str] = []
    where: list[int] = []
    length = 0
    for i, char in enumerate(text):
        where.append(length)
        if char == "\r":
            char = "" if text[i + 1 : i + 2] == "\n" else "\n"
        elif char == "\t":
            char = "    "
        elif category(char) in ("Zl", "Zp"):
            char = "\n"
        elif char != "\n" and category(char) in ("Cc", "Cs"):
            char = ""
        out.append(char)
        length += len(char)
    where.append(length)
    return "".join(out), where


def sanitize(text: str) -> str:
    """Drop control characters (so no escape sequences survive) and lone surrogates.

    Format characters such as ZWNJ and ZWJ are kept: Persian and emoji need them.
    Bidi controls are consumed by the layout engine rather than the terminal.
    """
    return sanitize_map(text)[0]


def sanitize_line(text: str) -> str:
    """Single-line variant for names and titles."""
    return " ".join(sanitize(text).split("\n")).strip()
