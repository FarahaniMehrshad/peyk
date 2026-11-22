"""An editable text with a caret that moves the way the text looks.

The text is kept in logical order. Left and right follow the screen, so in a
right-to-left line Left moves on and Right moves back.
"""

from __future__ import annotations

from unicodedata import bidirectional, category

from peyk.text.bidi import ZWNJ, LamAlef, Mode, VisualLine, boundaries, layout

UNDO_STEPS = 200
_EMOJI_MARKS = frozenset({*map(chr, range(0xFE00, 0xFE10)), chr(0x20E3)})


class Editor:
    def __init__(self, mode: Mode = "reorder", lam_alef: LamAlef = "stretch") -> None:
        self._mode: Mode = mode
        self._lam_alef: LamAlef = lam_alef
        self.text = ""
        self.caret = 0
        self.width = 40
        self._goal: int | None = None  # screen column that up and down aim for
        self._rows: list[VisualLine] = []
        self._rows_key: tuple[str, int] | None = None
        self._undo: list[tuple[str, int]] = []
        self._redo: list[tuple[str, int]] = []
        self._last_edit = ""

    # -- layout -------------------------------------------------------------

    @property
    def rows(self) -> list[VisualLine]:
        """One column is left free for the caret after the last character."""
        key = (self.text, self.width)
        if key != self._rows_key:
            room = max(1, self.width - 1)
            self._rows = layout(self.text, room, self._mode, lam_alef=self._lam_alef)
            self._rows_key = key
        return self._rows

    @property
    def caret_row(self) -> int:
        rows = self.rows
        index = len(rows) - 1
        while index > 0 and rows[index].start > self.caret:
            index -= 1
        return index

    def pad(self, row: VisualLine) -> int:
        """Columns to the left of a right-aligned row."""
        return max(0, self.width - row.width) if row.rtl else 0

    @property
    def caret_column(self) -> int:
        row = self.rows[self.caret_row]
        return max(0, self.pad(row) + row.caret_column(self.caret))

    def _continues(self, index: int) -> bool:
        """The row is wrapped, not ended: its end belongs to the next one."""
        rows = self.rows
        return index + 1 < len(rows) and rows[index + 1].start == rows[index].end

    def _last_in(self, index: int) -> int:
        """Last place for the caret in a row."""
        row = self.rows[index]
        if self._continues(index) and row.cells:
            return max(cell.start for cell in row.cells)
        return row.end

    def _index_at(self, index: int, screen_column: int) -> int:
        row = self.rows[index]
        return min(row.index_at(screen_column - self.pad(row)), self._last_in(index))

    def _stops(self, index: int) -> list[tuple[int, int]]:
        """(column, text index) of every place for the caret, left to right."""
        row = self.rows[index]
        stops = []
        column = 0
        for cell in row.cells:
            if cell.width:
                stops.append((column, cell.start))
            column += cell.width
        if not self._continues(index):
            if row.rtl:
                stops.insert(0, (-1, row.end))
            else:
                stops.append((column, row.end))
        return stops

    # -- moving -------------------------------------------------------------

    def _go(self, index: int) -> None:
        self.caret = max(0, min(index, len(self.text)))
        self._goal = None
        self._last_edit = ""

    def left(self) -> None:
        self._sideways(-1)

    def right(self) -> None:
        self._sideways(1)

    def _sideways(self, step: int) -> None:
        index = self.caret_row
        row = self.rows[index]
        stops = self._stops(index)
        if stops:
            column = row.caret_column(self.caret)
            here = min(range(len(stops)), key=lambda i: abs(stops[i][0] - column))
            if 0 <= here + step < len(stops):
                self._go(stops[here + step][1])
                return
        onward = step < 0 if row.rtl else step > 0
        if onward and index + 1 < len(self.rows):
            self._go(self.rows[index + 1].start)
        elif not onward and index > 0:
            self._go(self._last_in(index - 1))

    def word_left(self) -> None:
        self._word(-1)

    def word_right(self) -> None:
        self._word(1)

    def _word(self, step: int) -> None:
        onward = step < 0 if self.rows[self.caret_row].rtl else step > 0
        self._go(self._next_word() if onward else self._word_start())

    def _word_start(self) -> int:
        i = self.caret
        while i and not _in_word(self.text[i - 1]):
            i -= 1
        while i and _in_word(self.text[i - 1]):
            i -= 1
        return i

    def _next_word(self) -> int:
        i, end = self.caret, len(self.text)
        while i < end and _in_word(self.text[i]):
            i += 1
        while i < end and not _in_word(self.text[i]):
            i += 1
        return i

    def up(self) -> bool:
        return self._vertical(-1)

    def down(self) -> bool:
        return self._vertical(1)

    def _vertical(self, step: int) -> bool:
        target = self.caret_row + step
        if not 0 <= target < len(self.rows):
            return False
        goal = self.caret_column if self._goal is None else self._goal
        self._go(self._index_at(target, goal))
        self._goal = goal
        return True

    def home(self) -> None:
        self._go(self.rows[self.caret_row].start)

    def end(self) -> None:
        self._go(self._last_in(self.caret_row))

    def start_of_text(self) -> None:
        self._go(0)

    def end_of_text(self) -> None:
        self._go(len(self.text))

    def click(self, row: int, column: int) -> None:
        self._go(self._index_at(max(0, min(row, len(self.rows) - 1)), column))

    # -- editing ------------------------------------------------------------

    def set(self, text: str) -> None:
        """Replace everything, forgetting the past."""
        self.text = text
        self._undo.clear()
        self._redo.clear()
        self._go(len(text))

    def _replace(self, start: int, end: int, new: str, kind: str) -> None:
        if start == end and not new:
            return
        # Typing is undone a word at a time, everything else step by step.
        if not (kind == "type" and self._last_edit == "type"):
            self._undo.append((self.text, self.caret))
            del self._undo[:-UNDO_STEPS]
        self._redo.clear()
        self.text = self.text[:start] + new + self.text[end:]
        self._go(start + len(new))
        self._last_edit = "" if kind == "type" and new.isspace() else kind

    def insert(self, text: str) -> None:
        kind = "type" if len(text) == 1 else "paste"
        self._replace(self.caret, self.caret, text, kind)

    def backspace(self) -> None:
        if not self.caret:
            return
        last = self.text[self.caret - 1]
        if last == ZWNJ or _is_letter_mark(last):
            start = self.caret - 1  # just the mark, not the letter under it
        else:
            start = max(b for b in self._boundaries() if b < self.caret)
        self._replace(start, self.caret, "", "backspace")

    def delete(self) -> None:
        if self.caret < len(self.text):
            end = min(b for b in self._boundaries() if b > self.caret)
            self._replace(self.caret, end, "", "delete")

    def _boundaries(self) -> list[int]:
        """Edges of the characters, as people count them, around the caret."""
        start = self.text.rfind("\n", 0, self.caret) + 1
        end = self.text.find("\n", self.caret)
        end = len(self.text) if end < 0 else end
        edges = [start + b for b in boundaries(self.text[start:end])]
        return [max(0, start - 1), *edges, end + 1]

    def delete_word(self) -> None:
        self._replace(self._word_start(), self.caret, "", "word")

    def delete_to_start(self) -> None:
        start = self.text.rfind("\n", 0, self.caret) + 1
        self._replace(start, self.caret, "", "line")

    def delete_to_end(self) -> None:
        end = self.text.find("\n", self.caret)
        self._replace(self.caret, len(self.text) if end < 0 else end, "", "line")

    def undo(self) -> None:
        self._restore(self._undo, self._redo)

    def redo(self) -> None:
        self._restore(self._redo, self._undo)

    def _restore(self, source: list[tuple[str, int]], other: list[tuple[str, int]]) -> None:
        if source:
            other.append((self.text, self.caret))
            self.text, caret = source.pop()
            self._go(caret)


def _in_word(char: str) -> bool:
    return char.isalnum() or char in ("_", ZWNJ) or category(char) == "Mn"


def _is_letter_mark(char: str) -> bool:
    """A combining mark, but not part of an emoji."""
    return bidirectional(char) == "NSM" and char not in _EMOJI_MARKS
