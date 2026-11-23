"""The line above the compose box. It says who is typing, so nothing has to move for that."""

from __future__ import annotations

from textual.strip import Strip
from textual.widget import Widget

from peyk.ui.widgets.strips import Row, TextOptions, ink


class Divider(Widget):
    COMPONENT_CLASSES = {"divider--line", "divider--active", "divider--text"}
    DEFAULT_CSS = """
    Divider { height: 1; }
    Divider > .divider--line { color: $panel; }
    Divider > .divider--active { color: $edge; }
    Divider > .divider--text { color: $accent; }
    """

    def __init__(self, options: TextOptions) -> None:
        super().__init__()
        self._options = options
        self.pieces: list[str] = []
        self.active = False

    def show(self, pieces: list[str]) -> None:
        if pieces != self.pieces:
            self.pieces = list(pieces)
            self.refresh()

    def light(self, active: bool) -> None:
        if active != self.active:
            self.active = active
            self.refresh()

    def render_line(self, y: int) -> Strip:
        row = Row(self.size.width, self.rich_style)
        line = ink(self, "divider--active" if self.active else "divider--line")
        if self.pieces and row.left > 8:
            row.add("─", 1, line).space(1)
            for i, piece in enumerate(self.pieces):
                # Names give way to what follows them.
                rest = sum(map(len, self.pieces[i + 1 :]))
                text, cells = self._options.fit(piece, max(6, row.left - 2 - rest))
                row.add(text, cells, ink(self, "divider--text"))
            row.space(1)
        return row.add("─" * row.left, row.left, line).strip()
