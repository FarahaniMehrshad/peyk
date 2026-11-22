"""One line of numbered tabs, used for folders and for open chats."""

from __future__ import annotations

from dataclasses import dataclass

from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from peyk.text import format as fmt
from peyk.ui.widgets.strips import Row, TextOptions, ink


@dataclass(frozen=True, slots=True)
class Tab:
    id: int
    key: str
    title: str
    badge: int = 0


class TabBar(Widget):
    COMPONENT_CLASSES = {"tabbar--active", "tabbar--badge", "tabbar--key"}
    DEFAULT_CSS = """
    TabBar { height: 1; color: $text-muted; }
    TabBar > .tabbar--active { color: $primary; text-style: bold; }
    TabBar > .tabbar--badge { color: $warning; }
    TabBar > .tabbar--key { color: $text-muted; }
    """
    MAX_TITLE = 18

    class Chosen(Message):
        def __init__(self, bar: TabBar, tab_id: int) -> None:
            super().__init__()
            self.bar = bar
            self.tab_id = tab_id

        @property
        def control(self) -> TabBar:
            return self.bar

    def __init__(self, options: TextOptions, id: str, empty: str = "") -> None:  # noqa: A002
        super().__init__(id=id)
        self._options = options
        self._empty = empty
        self.tabs: list[Tab] = []
        self.active: int | None = None
        self._zones: list[tuple[int, int, int]] = []

    def show(self, tabs: list[Tab], active: int | None) -> None:
        if tabs != self.tabs or active != self.active:
            self.tabs, self.active = list(tabs), active
            self.refresh()

    def _cells(self, tab: Tab) -> tuple[str, str, int, str]:
        title, width = self._options.fit(tab.title, self.MAX_TITLE)
        badge = f" {fmt.count(tab.badge)}" if tab.badge else ""
        key = f"{tab.key} " if tab.key else ""
        return key, title, width, badge

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        row = Row(width, self.rich_style)
        self._zones = []
        if not self.tabs:
            text, cells = self._options.fit(self._empty, width)
            return row.add(text, cells).strip()
        drawn = [self._cells(tab) for tab in self.tabs]
        sizes = [len(k) + w + len(b) + 2 for k, _, w, b in drawn]
        # Skip leading tabs until the active one fits.
        active = next((i for i, t in enumerate(self.tabs) if t.id == self.active), 0)
        first = 0
        while first < active and sum(sizes[first : active + 1]) > width - (2 if first else 0):
            first += 1
        if first:
            row.add("< ", 2, ink(self, "tabbar--key"))
        for i in range(first, len(self.tabs)):
            key, title, cells, badge = drawn[i]
            if sizes[i] - 2 > row.left:
                row.add(">", 1, ink(self, "tabbar--key"))
                break
            start = row.used
            is_active = self.tabs[i].id == self.active
            style = ink(self, "tabbar--active")
            row.add(key, len(key), ink(self, "tabbar--key"))
            row.add(title, cells, style if is_active else None)
            row.add(badge, len(badge), ink(self, "tabbar--badge"))
            self._zones.append((start, row.used, self.tabs[i].id))
            row.space(min(2, row.left))
        return row.strip()

    def on_click(self, event: events.Click) -> None:
        for start, end, tab_id in self._zones:
            if start <= event.x < end:
                self.post_message(self.Chosen(self, tab_id))
