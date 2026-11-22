"""One-line status bar: connection and notices on the left, key hints on the right."""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from peyk.backend.base import Link

_LOOK = {
    Link.ONLINE: ("●", "online", "green"),
    Link.CONNECTING: ("◌", "connecting", "yellow"),
    Link.OFFLINE: ("○", "offline", "red"),
}


class StatusBar(Widget):
    DEFAULT_CSS = """
    StatusBar { dock: bottom; height: 1; background: $bar; color: $text-muted; padding: 0 1; }
    """
    link: reactive[Link] = reactive(Link.OFFLINE)
    hints: reactive[str] = reactive("")
    notice: reactive[str] = reactive("")
    mode: reactive[str] = reactive("")

    def render(self) -> Text:
        mark, name, colour = _LOOK[self.link]
        width = self.content_size.width
        left = Text.assemble((mark, colour), " ", name, end="", no_wrap=True)
        if self.mode:
            left = Text.assemble(
                (f" {self.mode} ", "reverse"), " ", (mark, colour), " ", name, end="", no_wrap=True
            )
        if self.notice:
            left.append(f"  {self.notice}", "yellow")
        gap = width - left.cell_len - Text(self.hints).cell_len
        if gap >= 2:
            left.append(" " * gap + self.hints)
        left.truncate(width, overflow="ellipsis")
        return left
