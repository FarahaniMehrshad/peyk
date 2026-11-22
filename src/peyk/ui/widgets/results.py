"""A list to pick from: one line per entry, or two when it has a detail."""

from __future__ import annotations

from dataclasses import dataclass

from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from peyk.ui.widgets.strips import Row, TextOptions, ink


@dataclass(frozen=True, slots=True)
class Entry:
    title: str
    value: object
    who: str = ""  # after the title, in its own direction
    right: str = ""
    detail: str = ""


class ResultList(Widget):
    COMPONENT_CLASSES = {
        "results--selected", "results--bar", "results--title", "results--who", "results--dim",
    }  # fmt: skip
    DEFAULT_CSS = """
    ResultList { height: 1fr; }
    ResultList > .results--selected { background: $surface; }
    ResultList > .results--bar { color: $primary; }
    ResultList > .results--title { text-style: bold; }
    ResultList > .results--who { color: $secondary; }
    ResultList > .results--dim { color: $text-muted; }
    """

    class Chosen(Message):
        pass

    class NearEnd(Message):
        pass

    def __init__(self, options: TextOptions, id: str = "results") -> None:  # noqa: A002
        super().__init__(id=id)
        self._options = options
        self.entries: list[Entry] = []
        self.index = 0
        self.note = ""
        self._top = 0

    # -- state

    def show(self, entries: list[Entry], note: str = "") -> None:
        """A new list: start at its first entry."""
        self.index = self._top = 0
        self._set(entries, note)

    def extend(self, entries: list[Entry], note: str = "") -> None:
        self._set(self.entries + entries, note)

    def _set(self, entries: list[Entry], note: str) -> None:
        self.entries, self.note = entries, note
        self._keep_visible()
        self.refresh()

    def set_note(self, note: str) -> None:
        self.note = note
        self.refresh()

    @property
    def chosen(self) -> Entry | None:
        return self.entries[self.index] if self.index < len(self.entries) else None

    @property
    def _height(self) -> int:
        return 2 if any(e.detail for e in self.entries) else 1

    @property
    def page(self) -> int:
        return max(1, self.size.height // self._height)

    def move(self, step: int) -> None:
        if self.entries:
            self.index = max(0, min(len(self.entries) - 1, self.index + step))
            self._keep_visible()
            self.refresh()
            if self.index >= len(self.entries) - 5:
                self.post_message(self.NearEnd())

    def _keep_visible(self) -> None:
        self._top = max(self.index - self.page + 1, min(self._top, self.index))
        self._top = max(0, self._top)

    def on_resize(self) -> None:
        self._keep_visible()

    # -- drawing

    def _style(self, name: str):
        return ink(self, f"results--{name}")

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        row = Row(width, self.rich_style)
        index, part = self._top + y // self._height, y % self._height
        if index >= len(self.entries):
            # The note follows the last entry, or stands alone.
            if self.note and index == len(self.entries) and part == 0:
                text, cells = self._options.fit(self.note, width - 2)
                row.space(1).add(text, cells, self._style("dim"))
            return row.strip()
        entry = self.entries[index]
        if index == self.index:
            selected = self.get_component_rich_style("results--selected").bgcolor
            row.base += type(row.base)(bgcolor=selected)
        row.add("▌" if index == self.index else " ", 1, self._style("bar"))
        if part == 1:
            text, cells = self._options.fit(entry.detail, row.left - 2)
            return row.space(1).add(text, cells, self._style("dim")).strip()
        right, right_cells = self._options.fit(entry.right, max(0, row.left // 2))
        text, cells = self._options.fit(entry.title, row.left - right_cells - 2)
        row.add(text, cells, self._style("title"))
        if entry.who:
            who, cells = self._options.fit(entry.who, row.left - right_cells - 4)
            row.space(2).add(who, cells, self._style("who"))
        row.space(row.left - right_cells - 1)
        return row.add(right, right_cells, self._style("dim")).strip()

    # -- mouse

    def on_click(self, event: events.Click) -> None:
        index = self._top + event.y // self._height
        if index < len(self.entries):
            self.index = index
            self.refresh()
            self.post_message(self.Chosen())

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.move(1)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.move(-1)
