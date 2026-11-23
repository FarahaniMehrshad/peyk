"""The line above the messages that shows a pinned one."""

from __future__ import annotations

from textual import events
from textual.message import Message as UiMessage
from textual.strip import Strip
from textual.widget import Widget

from peyk.models import Message
from peyk.text import format as fmt
from peyk.ui.widgets.strips import Row, TextOptions, ink


class PinBar(Widget):
    COMPONENT_CLASSES = {"pinbar--label", "pinbar--text"}
    DEFAULT_CSS = """
    PinBar { height: 1; padding: 0 1; display: none; }
    PinBar > .pinbar--label { color: $primary; text-style: bold; }
    PinBar > .pinbar--text { color: $text-muted; }
    """

    class Clicked(UiMessage):
        pass

    def __init__(self, options: TextOptions) -> None:
        super().__init__()
        self._options = options
        self.message: Message | None = None
        self._place = ""

    def show(self, message: Message | None, index: int = 0, count: int = 0) -> None:
        """`index` counts from the newest."""
        self.message = message
        self._place = f" {index + 1} of {count}" if count > 1 else ""
        self.display = message is not None
        self.refresh()

    def render_line(self, y: int) -> Strip:
        row = Row(self.content_size.width, self.rich_style)
        if self.message is None:
            return row.strip()
        label = f"Pinned{self._place}  "
        row.add(label, len(label), ink(self, "pinbar--label"))
        text = fmt.body(self.message) or "(empty message)"
        shown, cells = self._options.fit(text, max(1, row.left))
        row.add(shown, cells, ink(self, "pinbar--text"))
        return row.strip()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Clicked())
