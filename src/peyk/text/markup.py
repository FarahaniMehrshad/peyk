"""Formatting typed as plain characters: **bold**, __italic__, `code` and so on.

Follows the official apps: marks only count at the edges of words, so paths and
identifiers such as a__b or file__name.py are left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from peyk.models import Span

_MARKS = {"**": "bold", "__": "italic", "~~": "strike", "||": "spoiler"}
_OPENS_AFTER = frozenset("([{<\"'«“‘-—–…")
_CLOSES_BEFORE = frozenset(".,:;!?…)]}>\"'»”’-—–،؛؟")
_ENDS_SENTENCE = frozenset(".,:;!?")
_LINK = re.compile(r"\[([^\[\]\n]+)\]\(((?:https?|tg)://[^\s()]+)\)")
_LANGUAGE = re.compile(r"[A-Za-z0-9_+#-]{1,20}\n")


@dataclass(frozen=True, slots=True)
class Parsed:
    text: str
    spans: tuple[Span, ...]
    where: list[int]  # for each index of the input (and its end), where it went


def parse(text: str, typed: list[bool] | None = None) -> Parsed:
    """With `typed`, only characters flagged there can be marks."""
    return _Scanner(text, typed).run()


class _Scanner:
    def __init__(self, text: str, typed: list[bool] | None) -> None:
        self.text = text
        self.typed = typed
        self.drop = [False] * len(text)
        self.spans: list[Span] = []
        self.closings: set[int] = set()

    def run(self) -> Parsed:
        i = 0
        while i < len(self.text):
            i = self._step(i)
        return self._finish()

    def _is_mark(self, start: int, end: int) -> bool:
        return self.typed is None or all(self.typed[start:end])

    def _remove(self, start: int, end: int) -> None:
        self.drop[start:end] = [True] * (end - start)

    def _step(self, i: int) -> int:
        text = self.text
        if i in self.closings:
            return i + 2
        if text.startswith("```", i) and self._is_mark(i, i + 3):
            return self._block(i)
        if text[i] == "`" and self._is_mark(i, i + 1):
            return self._code(i)
        if text[i] == "[" and (link := _LINK.match(text, i)):
            if self._is_mark(i, i + 1) and self._is_mark(link.end(1), link.end()):
                self.spans.append(Span(link.start(1), link.end(1), "link", link[2]))
                self._remove(i, i + 1)
                self._remove(link.end(1), link.end())
                return link.end()
        mark = text[i : i + 2]
        if mark in _MARKS and self._is_mark(i, i + 2) and self._opens(i):
            end = self._closing(mark, i)
            if end is not None:
                self.spans.append(Span(i + 2, end, _MARKS[mark]))
                self._remove(i, i + 2)
                self._remove(end, end + 2)
                self.closings.add(end)
                return i + 2
        return i + 1

    def _block(self, i: int) -> int:
        end = self.text.find("```", i + 3)
        if end < 0 or not self.text[i + 3 : end].strip() or not self._is_mark(end, end + 3):
            return i + 3
        start, stop = i + 3, end
        language = ""
        if found := _LANGUAGE.match(self.text, start, end):
            language, start = found[0].strip(), found.end()
        elif self.text[start] == "\n":
            start += 1
        if self.text[stop - 1] == "\n" and stop - 1 > start:
            stop -= 1
        self.spans.append(Span(start, stop, "pre", language))
        self._remove(i, start)
        self._remove(stop, end + 3)
        return end + 3

    def _code(self, i: int) -> int:
        end = self.text.find("`", i + 1)
        content = self.text[i + 1 : end] if end > 0 else ""
        if not content.strip() or "\n" in content or not self._is_mark(end, end + 1):
            return i + 1
        self.spans.append(Span(i + 1, end, "code"))
        self._remove(i, i + 1)
        self._remove(end, end + 1)
        return end + 1

    def _opens(self, i: int) -> bool:
        text = self.text
        before = text[i - 1] if i else " "
        after = text[i + 2 : i + 3]
        at_edge = before.isspace() or before in _OPENS_AFTER or (i > 0 and self.drop[i - 1])
        return at_edge and bool(after) and not after.isspace() and after != text[i]

    def _closing(self, mark: str, i: int) -> int | None:
        text = self.text
        end = text.find(mark, i + 3)
        while end >= 0:
            if "\n\n" in text[i:end]:
                return None
            before, after = text[end - 1], text[end + 2 : end + 3]
            at_edge = not after or after.isspace() or after in _CLOSES_BEFORE
            # Another mark may follow directly, as in **__both__**.
            at_edge = at_edge or text[end + 2 : end + 4] in _MARKS
            # __init__.py is a name, not an italic "init".
            mid_word = after in _ENDS_SENTENCE and text[end + 3 : end + 4].isalnum()
            closes = not before.isspace() and at_edge and not (after and mid_word)
            if closes and self._is_mark(end, end + 2):
                return end
            end = text.find(mark, end + 1)
        return None

    def _finish(self) -> Parsed:
        kept = [c for c, gone in zip(self.text, self.drop, strict=True) if not gone]
        where: list[int] = []
        position = 0
        for gone in self.drop:
            where.append(position)
            position += not gone
        where.append(position)
        # Telegram trims messages, which would shift every offset.
        full = "".join(kept)
        lead = len(full) - len(full.lstrip())
        clean = full.strip()
        where = [min(max(w - lead, 0), len(clean)) for w in where]
        spans = []
        for span in sorted(self.spans, key=lambda s: (s.start, -s.end)):
            start, end = where[span.start], where[span.end]
            if start < end:
                spans.append(Span(start, end, span.kind, span.url))
        return Parsed(clean, tuple(spans), where)


@dataclass(frozen=True, slots=True)
class Revised:
    text: str
    spans: tuple[Span, ...]  # formatting typed during the edit
    carried: list[tuple[int, int] | None]  # where each old range is now


def revise(
    old: str, ranges: list[tuple[int, int]], new: str, formatting: bool = True
) -> Revised:
    """An edit of `old`. Its formatting (`ranges`) moves along with the words.

    Only newly typed characters can be marks: what was literal stays literal.
    """
    match = _align(old, new)
    typed = [formatting] * len(new)
    for j in match:
        if j is not None:
            typed[j] = False
    parsed = parse(new, typed)
    carried: list[tuple[int, int] | None] = []
    for start, end in ranges:
        kept = [j for j in match[start:end] if j is not None]
        if not kept:
            carried.append(None)
            continue
        a, b = parsed.where[kept[0]], parsed.where[kept[-1] + 1]
        carried.append((a, b) if a < b else None)
    return Revised(parsed.text, parsed.spans, carried)


def _align(old: str, new: str) -> list[int | None]:
    """For each character of `old`, its index in `new`."""
    shorter = min(len(old), len(new))
    head = 0
    while head < shorter and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < shorter - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    match: list[int | None] = list(range(head)) + [None] * (len(old) - head)
    middle_old, middle_new = old[head : len(old) - tail], new[head : len(new) - tail]
    if middle_old and middle_new:
        blocks = SequenceMatcher(None, middle_old, middle_new, autojunk=False)
        for a, b, size in blocks.get_matching_blocks():
            for k in range(size):
                match[head + a + k] = head + b + k
    for k in range(tail):
        match[len(old) - 1 - k] = len(new) - 1 - k
    return match


def utf16_length(text: str) -> int:
    """Telegram's unit for offsets and limits."""
    return len(text) + sum(ord(c) > 0xFFFF for c in text)
