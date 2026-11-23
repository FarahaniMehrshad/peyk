"""One picture, as large as the window allows."""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Static

from peyk.backend.base import BackendError
from peyk.images import Box, fit
from peyk.keys import hints
from peyk.models import Message
from peyk.text import format as fmt
from peyk.ui.gallery import Gallery
from peyk.ui.keymap import action_for, pressed
from peyk.ui.widgets.bidi_label import BidiLabel
from peyk.ui.widgets.sixels import SixelLayer, Spot
from peyk.ui.widgets.strips import TextOptions, backdrop

GROW = 3.0


class Picture(Widget):
    DEFAULT_CSS = "Picture { height: 1fr; }"

    def __init__(self, gallery: Gallery) -> None:
        super().__init__(id="picture")
        self._gallery = gallery
        self._message: Message | None = None
        self._rows: list[Strip] = []
        self._layer = SixelLayer(self, self._spots)

    def show(self, message: Message) -> None:
        self._message = message
        self._rows = []
        self.refresh()

    def _box(self) -> Box | None:
        media = self._message.media if self._message else None
        if media is None:
            return None
        cell = self._gallery.graphics.cell
        return fit(media.width, media.height, self.size.width, self.size.height, cell, GROW)

    def _corner(self, box: Box) -> tuple[int, int]:
        return (self.size.width - box.cols) // 2, (self.size.height - box.rows) // 2

    def on_resize(self) -> None:
        self._rows = []

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if not self._rows:
            self._rows = self._draw(width)
        return self._rows[y] if y < len(self._rows) else Strip.blank(width, self.rich_style)

    def _draw(self, width: int) -> list[Strip]:
        blank = Strip.blank(width, self.rich_style)
        lines = [blank] * max(1, self.size.height)
        box = self._box()
        if box is None or self._message is None:
            return lines
        assert self._message.media
        blocks = self._gallery.blocks(self._message.media, box, backdrop(self), large=True)
        if blocks is None:
            return lines
        left, top = self._corner(box)
        for i, row in enumerate(blocks):
            lines[top + i] = Strip.join(
                [
                    Strip.blank(left, self.rich_style),
                    Strip(row, box.cols),
                    Strip.blank(width - left - box.cols, self.rich_style),
                ]
            )
        return lines

    def render_lines(self, crop) -> list[Strip]:
        strips = super().render_lines(crop)
        self._layer.painted()
        return strips

    def _spots(self) -> list[Spot]:
        box = self._box()
        media = self._message.media if self._message else None
        if box is None or media is None or not self._gallery.graphics.sixel:
            return []
        image = self._gallery.best(media)
        if image is None:
            return []
        left, top = self._corner(box)
        background = backdrop(self)
        return [
            Spot(
                (media.id, image.size, box),
                self.region.x + left,
                self.region.y + top,
                lambda: self._gallery.sixel(media, image, box, background, 0, box.rows),
            )
        ]


class ViewerScreen(ModalScreen[Message | None]):
    """Dismissed with a message to open in another program, if any."""

    def __init__(
        self,
        gallery: Gallery,
        options: TextOptions,
        messages: list[Message],
        index: int,
        keys: dict[str, str],
    ) -> None:
        """`messages` all have a picture."""
        super().__init__()
        self._keys = keys
        self._gallery = gallery
        self._options = options
        self._messages = messages
        self._index = index
        self._note = ""

    def compose(self) -> ComposeResult:
        with Vertical(classes="viewer"):
            yield Picture(self._gallery)
            yield BidiLabel("", mode=self._options.mode, lam_alef=self._options.lam_alef,
                            id="viewer-text")  # fmt: skip
            yield Static("", markup=False, id="viewer-hints")

    def on_mount(self) -> None:
        self._show()

    @property
    def message(self) -> Message:
        return self._messages[self._index]

    def _show(self) -> None:
        self._note = "Loading...  "
        self.query_one(Picture).show(self.message)
        self._describe()
        self.run_worker(self._load(self.message), group="picture", exclusive=True)

    async def _load(self, message: Message) -> None:
        try:
            found = await self._gallery.large(message)
            self._note = "" if found else "This picture cannot be shown.  "
        except BackendError as error:
            self._note = f"{error.message}  "
        if self.is_attached and message is self.message:
            self.query_one(Picture).show(message)
            self._describe()

    def _describe(self) -> None:
        message = self.message
        who = "You" if message.outgoing else message.sender
        text = " ".join(message.text.split())
        line = f"{who}, {fmt.list_date(message.date)} {fmt.clock(message.date)}"
        self.query_one("#viewer-text", BidiLabel).update(f"{line}\n{text}" if text else line)
        place = f"{self._index + 1}/{len(self._messages)}  "
        parts = [("focus_list focus_chat", "previous/next"), ("open_media", "another program")]
        several = len(self._messages) > 1
        shown = hints(self._keys, *parts[0 if several else 1 :])
        self.query_one("#viewer-hints", Static).update(
            f"{self._note}{place if several else ''}{shown}  Esc close"
        )

    def on_key(self, event: events.Key) -> None:
        key = pressed(event)
        if key in ("escape", "q", "enter"):
            self.dismiss(None)
        elif key == " ":
            self._step(1)
        else:
            match action_for(event, self._keys):
                case "focus_list" | "up":
                    self._step(-1)
                case "focus_chat" | "down":
                    self._step(1)
                case "open_media":
                    self.dismiss(self.message)
                case "preview" | "quit" | "deselect":
                    self.dismiss(None)
                case _:
                    return
        event.stop()
        event.prevent_default()

    def _step(self, step: int) -> None:
        index = self._index + step
        if 0 <= index < len(self._messages):
            self._index = index
            self._show()

    def on_click(self) -> None:
        self.dismiss(None)
