"""Right-to-left text layout for terminals that don't do it themselves.

The unit of work is a *cluster*: one user-perceived character (base letter plus
its combining marks, or a whole emoji sequence). Bidi runs over clusters rather
than code points, so marks stay attached to their base and emoji survive
reversal. Every cluster remembers which slice of the original string it came
from, which is what lets a caret move logically while text is drawn visually.

Order of operations: cluster -> resolve levels (per paragraph) -> wrap
(logical order) -> reorder (per line), as UAX #9 requires.

Modes, from testing in Windows Terminal 1.25:
  reorder   Windows Terminal joins Arabic letters itself but never reorders
            them, so we only reorder. Default.
  shape     Also substitute pre-joined letterforms, for terminals that don't
            join. Looks poor in Windows Terminal: the font fallback is ragged.
  terminal  Right-align only, for terminals with full bidi support.
  off       Leave everything alone.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal
from unicodedata import bidirectional, category, mirrored

from arabic_reshaper.letters import LETTERS_ARABIC
from bidi import algorithm as _ba
from bidi.mirror import MIRRORED
from rich.cells import split_graphemes

Mode = Literal["reorder", "shape", "terminal", "off"]
LamAlef = Literal["stretch", "split", "font"]
Direction = Literal["ltr", "rtl"]

ZWNJ = "\u200c"
ZWJ = "\u200d"
# Stops letters joining, like ZWNJ, but draws nothing in any font. Fallback fonts
# tend to show ZWNJ as a bar over the neighbouring letter.
BREAK = "\u200b"
_KEYCAP = "\u20e3"
TATWEEL = "\u0640"
_LAMS = frozenset("\u0644\u06b5\u06b6\u06b7\u06b8")
_ALEFS = frozenset("\u0627\u0622\u0623\u0625\u0671")
_ISOLATED, _INITIAL, _MEDIAL, _FINAL = range(4)
_ISOLATE_CONTROLS = frozenset("\u2066\u2067\u2068\u2069")
_INVISIBLE = _ISOLATE_CONTROLS | frozenset(
    "\u200b\u200c\u200e\u200f\u061c\u202a\u202b\u202c\u202d\u202e\ufeff"
)


@dataclass(frozen=True, slots=True)
class Cluster:
    text: str  # what to draw
    start: int  # slice of the original string
    end: int
    width: int
    level: int = 0
    kind: str = "L"  # original bidi class
    space: bool = False


@dataclass(frozen=True, slots=True)
class VisualLine:
    """One screen row. `cells` run left to right; `start:end` is the logical slice."""

    cells: tuple[Cluster, ...]
    start: int
    end: int
    rtl: bool

    @property
    def text(self) -> str:
        return "".join(c.text for c in self.cells)

    @property
    def width(self) -> int:
        return sum(c.width for c in self.cells)

    def caret_column(self, index: int) -> int:
        """Column of the cell a block caret at logical `index` sits on.

        At the end of the line that is the free cell after the text: `width` in
        a left-to-right line and -1 in a right-to-left one.
        """
        column = 0
        best: tuple[int, int] | None = None
        for cell in self.cells:
            if cell.width:
                if cell.start <= index < cell.end:
                    return column
                # Invisible characters own no cell: use the next one that does.
                if cell.start > index and (best is None or cell.start < best[0]):
                    best = (cell.start, column)
            column += cell.width
        if best is not None:
            return best[1]
        return -1 if self.rtl else self.width

    def index_at(self, column: int) -> int:
        """Logical index for a click or vertical move landing on `column`."""
        position = 0
        if column >= 0:
            for cell in self.cells:
                if position <= column < position + cell.width:
                    return cell.start
                position += cell.width
        past_left = column < 0
        return self.end if past_left == self.rtl else self.start


def direction(text: str) -> Direction:
    """Direction of the first strong character; left-to-right if there is none."""
    for char in text:
        kind = bidirectional(char)
        if kind in ("R", "AL"):
            return "rtl"
        if kind == "L":
            return "ltr"
    return "ltr"


def shape(text: str) -> str:
    """Join Arabic-script letters. Always returns exactly len(text) characters."""
    out = list(text)
    for i, char in enumerate(text):
        forms = LETTERS_ARABIC.get(char)
        if forms is None or char == ZWJ:
            continue
        before = _neighbour(text, i, -1)
        after = _neighbour(text, i, 1)
        joins_before = bool(forms[_FINAL]) and before is not None and _joins_after(before)
        joins_after = bool(forms[_INITIAL]) and after is not None and _joins_before(after)
        if joins_before and joins_after:
            form = forms[_MEDIAL] or forms[_FINAL]
        elif joins_before:
            form = forms[_FINAL]
        elif joins_after:
            form = forms[_INITIAL]
        else:
            form = forms[_ISOLATED]
        out[i] = form or char
    return "".join(out)


def _neighbour(text: str, i: int, step: int) -> str | None:
    """Next character that matters for joining; combining marks are transparent."""
    i += step
    while 0 <= i < len(text):
        if category(text[i]) not in ("Mn", "Me"):
            return text[i]
        i += step
    return None


def _joins_after(char: str) -> bool:
    if char == ZWJ:
        return True
    forms = LETTERS_ARABIC.get(char)
    return bool(forms and (forms[_INITIAL] or forms[_MEDIAL]))


def _joins_before(char: str) -> bool:
    if char == ZWJ:
        return True
    forms = LETTERS_ARABIC.get(char)
    return bool(forms and (forms[_FINAL] or forms[_MEDIAL]))


def _is_regional(text: str) -> bool:
    return len(text) == 1 and "\U0001f1e6" <= text <= "\U0001f1ff"


def _drawable(glyph: str, width: int) -> tuple[str, int]:
    """Replace sequences Windows Terminal cannot draw: keycaps and flags."""
    if glyph.endswith(_KEYCAP):
        return glyph[0], 1
    if len(glyph) == 2 and all(_is_regional(c) for c in glyph):
        letters = "".join(chr(ord(c) - 0x1F1E6 + ord("A")) for c in glyph)
        return letters, 2
    return glyph, width


def _spans(text: str) -> list[tuple[int, int, int]]:
    """(start, end, width) of each cluster."""
    # A joiner after an Arabic letter would glue the next letter into one cluster.
    probe = "".join(
        ZWNJ if c == ZWJ and i and bidirectional(text[i - 1]) == "AL" else c
        for i, c in enumerate(text)
    )
    spans: list[tuple[int, int, int]] = []
    for start, end, width in split_graphemes(probe)[0]:
        if spans:
            p_start, p_end, p_width = spans[-1]
            # A flag is a pair of letters; reversing them would change the country.
            pair = _is_regional(text[start:end]) and _is_regional(text[p_start:p_end])
            if pair or (width == 0 and text[start] not in _INVISIBLE):
                spans[-1] = (p_start, end, p_width + width)
                continue
        spans.append((start, end, width))
    return spans


def boundaries(text: str) -> list[int]:
    """Where user-perceived characters begin, and the end of the text."""
    return [start for start, _, _ in _spans(text)] + [len(text)]


def _clusters(text: str, mode: Mode, lam_alef: LamAlef, offset: int = 0) -> list[Cluster]:
    """`offset` is added to the recorded positions."""
    if text.isascii() and text.isprintable():
        return [
            Cluster(c, offset + i, offset + i + 1, 1, 0, bidirectional(c), c == " ")
            for i, c in enumerate(text)
        ]
    ours = mode in ("reorder", "shape")
    drawn = shape(text) if mode == "shape" else text
    result: list[Cluster] = []
    lam = False  # previous cluster is a lam that the next letter may join
    for start, end, width in _spans(text):
        base = text[start]
        glyph = drawn[start:end]
        kind = "BN" if base in _ISOLATE_CONTROLS else bidirectional(base) or "L"
        if kind == "B":
            kind = "WS"
        if ours:
            hidden = _INVISIBLE | {ZWJ} if kind == "AL" else _INVISIBLE
            zwnj = kind == "AL" and ZWNJ in glyph
            glyph = "".join(c for c in glyph if c not in hidden)
            if not glyph:
                width = 0
            glyph, width = _drawable(glyph, width)
            if mode == "reorder":
                if zwnj:
                    # Drawn right to left, "after this letter" is on its left.
                    glyph = BREAK + glyph
                elif lam and base in _ALEFS:
                    # The two-cell lam-alef ligature falls apart on a cell grid.
                    if lam_alef == "stretch":
                        glyph, width = glyph + TATWEEL, width + 1
                    elif lam_alef == "split":
                        glyph += BREAK
                lam = base in _LAMS and not zwnj
        space = base in " \t\u3000"
        result.append(
            Cluster(glyph, offset + start, offset + end, width, 0, kind, space)
        )
    return result


def _resolve(clusters: list[Cluster], base_level: int) -> list[Cluster]:
    """Assign an embedding level to each cluster."""
    storage = _ba.get_empty_storage()
    storage["base_level"] = base_level
    storage["base_dir"] = ("L", "R")[base_level]
    storage["chars"] = [
        {"ch": "", "level": base_level, "type": c.kind, "orig": c.kind, "i": i}
        for i, c in enumerate(clusters)
    ]
    _ba.explicit_embed_and_overrides(storage)
    _ba.resolve_weak_types(storage)
    _ba.resolve_neutral_types(storage, False)
    _ba.resolve_implicit_levels(storage, False)

    levels: list[int | None] = [None] * len(clusters)
    for char in storage["chars"]:
        levels[char["i"]] = char["level"]
    # Characters dropped by rule X9 are invisible; keep them beside their neighbour.
    previous = base_level
    resolved = []
    for cluster, level in zip(clusters, levels, strict=True):
        previous = previous if level is None else level
        c = cluster
        resolved.append(Cluster(c.text, c.start, c.end, c.width, previous, c.kind, c.space))
    return resolved


def _wrap(clusters: list[Cluster], width: int) -> list[list[Cluster]]:
    lines: list[list[Cluster]] = []
    line: list[Cluster] = []
    used = 0
    last_space = -1
    for cluster in clusters:
        if line and not cluster.space and used + cluster.width > width:
            if last_space >= 0:
                head, line = line[: last_space + 1], line[last_space + 1 :]
            else:
                head, line = line, []
            lines.append(head)
            used = sum(c.width for c in line)
            last_space = -1
        line.append(cluster)
        used += cluster.width
        if cluster.space:
            last_space = len(line) - 1
    lines.append(line)
    return lines


def _trim(line: list[Cluster], width: int) -> list[Cluster]:
    """Let trailing spaces hang past the edge instead of forcing a new row."""
    used = sum(c.width for c in line)
    trimmed = list(line)
    for i in range(len(trimmed) - 1, -1, -1):
        if used <= width or not trimmed[i].space:
            break
        used -= trimmed[i].width
        trimmed[i] = replace(trimmed[i], text="", width=0)
    return trimmed


def _reorder(line: list[Cluster], base_level: int) -> list[Cluster]:
    cells = list(line)
    for i in range(len(cells) - 1, -1, -1):  # rule L1
        if cells[i].kind not in ("WS", "BN", "S"):
            break
        cells[i] = replace(cells[i], level=base_level)
    odd = [c.level for c in cells if c.level % 2]
    if odd:
        order = [{"level": c.level, "cell": c} for c in cells]
        highest = max(c.level for c in cells)
        _ba.reverse_contiguous_sequence(order, 0, len(order) - 1, highest, min(odd))
        cells = [entry["cell"] for entry in order]
    return [  # rule L4
        replace(c, text=MIRRORED.get(c.text, c.text))
        if c.level % 2 and len(c.text) == 1 and mirrored(c.text)
        else c
        for c in cells
    ]


def fit(
    text: str, width: int, mode: Mode = "reorder", lam_alef: LamAlef = "stretch"
) -> VisualLine:
    """One line of at most `width` cells, ending in an ellipsis if cut."""
    text = text.replace("\n", " ")
    if width <= 0:
        return VisualLine((), 0, 0, False)
    clusters = _clusters(text, mode, lam_alef)
    if sum(c.width for c in clusters) > width:
        used, end = 0, 0
        for cluster in clusters:
            if used + cluster.width > width - 1:
                break
            used += cluster.width
            end = cluster.end
        text = text[:end].rstrip() + "…"
    return layout(text, width, mode, lam_alef=lam_alef)[0]


def layout(
    text: str,
    width: int,
    mode: Mode = "reorder",
    base: Direction | None = None,
    lam_alef: LamAlef = "stretch",
) -> list[VisualLine]:
    """Wrap `text` to `width` cells and put each row in visual order."""
    width = max(1, width)
    ours = mode in ("reorder", "shape")
    lines: list[VisualLine] = []
    offset = 0
    for paragraph in text.split("\n"):
        rtl = mode != "off" and (base or direction(paragraph)) == "rtl"
        clusters = _clusters(paragraph, mode, lam_alef, offset)
        simple = not rtl and paragraph.isascii()  # nothing to reorder
        if ours and clusters and not simple:
            clusters = _resolve(clusters, int(rtl))
        cursor = offset
        for row in _wrap(clusters, width):
            end = row[-1].end if row else cursor
            row = _trim(row, width)
            cells = _reorder(row, int(rtl)) if ours and row and not simple else row
            if cells and cells[0].text.startswith(BREAK):
                # Nothing to its left to join, and after a box-drawing character
                # Windows Terminal would shift the rest of the row.
                cells = [replace(cells[0], text=cells[0].text[1:]), *cells[1:]]
            lines.append(VisualLine(tuple(cells), cursor, end, rtl))
            cursor = end
        offset += len(paragraph) + 1
    return lines
