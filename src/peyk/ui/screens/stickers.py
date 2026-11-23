"""Choosing a sticker or a GIF of one's own collection."""

from __future__ import annotations

from dataclasses import replace
from functools import partial

from rich.style import Style
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message as UiMessage
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from peyk import keys as keyboard
from peyk.backend.base import Backend, BackendError
from peyk.images import Backdrop, Box
from peyk.models import Media, StickerPack
from peyk.text import format as fmt
from peyk.ui.gallery import Gallery
from peyk.ui.keymap import action_for, pressed
from peyk.ui.widgets.sixels import SixelLayer, Spot
from peyk.ui.widgets.strips import Row, TextOptions, backdrop

STICKER_CELL = (10, 5)  # columns and rows of one, with a line for its emoji
GIF_CELL = (16, 6)
LOOK = 0.05  # seconds of stillness before pictures are asked for
NO_STICKERS = "You have no stickers yet. Add some in an official app."
NO_GIFS = "You have no saved GIFs yet. Save some in an official app."


def emoji_of(media: Media) -> str:
    """Of a sticker, as its label has it."""
    return fmt.emoji(media.label.removeprefix("Sticker").strip())


class PackList(Widget):
    COMPONENT_CLASSES = {"packs--selected"}
    DEFAULT_CSS = """
    PackList { width: 24; height: 1fr; border-right: solid $panel; }
    PackList > .packs--selected { color: $on-primary; background: $primary; }
    """

    class Clicked(UiMessage):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, options: TextOptions) -> None:
        super().__init__()
        self._options = options
        self.packs: list[StickerPack] = []
        self.index = 0

    def show(self, packs: list[StickerPack], index: int) -> None:
        self.packs, self.index = packs, index
        self.refresh()

    @property
    def _top(self) -> int:
        shown = max(1, self.size.height)
        return max(0, min(self.index - shown // 2, len(self.packs) - shown))

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        row = Row(width, self.rich_style)
        i = self._top + y
        if i < len(self.packs):
            lit = self.get_component_rich_style("packs--selected") if i == self.index else None
            label, cells = self._options.fit(self.packs[i].title, max(1, width - 2))
            row.add(" ", 1, lit).add(label, cells, lit)
            row.add(" " * row.left, row.left, lit)
        return row.strip()

    def on_click(self, event: events.Click) -> None:
        index = self._top + event.y
        if 0 <= index < len(self.packs):
            self.post_message(self.Clicked(index))


class StickerGrid(Widget):
    COMPONENT_CLASSES = {"grid--selected", "grid--label"}
    DEFAULT_CSS = """
    StickerGrid { width: 1fr; height: 1fr; padding-left: 1; }
    StickerGrid > .grid--selected { color: $on-primary; background: $primary; }
    StickerGrid > .grid--label { color: $text-muted; }
    """

    class Moved(UiMessage):
        pass

    class Chosen(UiMessage):
        pass

    def __init__(self, gallery: Gallery | None, options: TextOptions, wide: bool) -> None:
        """Without a gallery, there are only emoji."""
        super().__init__()
        self._gallery = gallery
        self._options = options
        self._cell = GIF_CELL if wide else STICKER_CELL
        self.items: tuple[Media, ...] = ()
        self.index = 0
        self._first = 0  # the first row of cells in view
        self._layer = SixelLayer(self, self._spots)
        self._look_timer: Timer | None = None

    def show(self, items: tuple[Media, ...]) -> None:
        self.items, self.index, self._first = items, 0, 0
        self.refresh()

    @property
    def selected(self) -> Media | None:
        return self.items[self.index] if self.items else None

    @property
    def _columns(self) -> int:
        return max(1, self.content_size.width // self._cell[0])

    @property
    def _rows_shown(self) -> int:
        return max(1, self.content_size.height // self._cell[1])

    def _keep_in_view(self) -> None:
        row, shown = self.index // self._columns, self._rows_shown
        rows = -(-len(self.items) // self._columns)
        if row < self._first:
            self._first = row
        elif row >= self._first + shown:
            self._first = row - shown + 1
        self._first = max(0, min(self._first, rows - shown))

    def move(self, step: int, rows: int = 0) -> None:
        if not self.items:
            return
        columns, last = self._columns, len(self.items) - 1
        target = self.index + step + rows * columns
        if rows and target > last and self.index // columns < last // columns:
            target = last  # into a shorter last row
        elif rows and target < 0 and self.index >= columns:
            target = self.index % columns
        if 0 <= target <= last and target != self.index:
            self.index = target
            self._keep_in_view()
            self.refresh()
            self.post_message(self.Moved())

    def page(self, direction: int) -> None:
        self.move(0, rows=direction * self._rows_shown)

    # -- drawing --

    def _box(self, media: Media) -> Box | None:
        if self._gallery is None:
            return None
        cols, rows = self._cell
        return self._gallery.box(media, cols - 2, rows - 1)

    def _background(self, selected: bool) -> Backdrop:
        if not selected:
            return backdrop(self)
        colour = self.get_component_rich_style("grid--selected").bgcolor
        return tuple(colour.triplet) if colour and colour.triplet else None

    def render_line(self, y: int) -> Strip:
        row = Row(self.size.width, self.rich_style)
        cell_rows = self._cell[1]
        shown, line = divmod(y, cell_rows)
        if shown < self._rows_shown:
            first = (self._first + shown) * self._columns
            for i in range(first, min(first + self._columns, len(self.items))):
                self._cell_line(row, self.items[i], i == self.index, line)
        return row.strip()

    def _cell_line(self, row: Row, media: Media, selected: bool, line: int) -> None:
        cols, rows = self._cell
        lit = self.get_component_rich_style("grid--selected") if selected else Style()
        box = self._box(media)
        sticker = media.kind == "sticker"
        if box is None:  # no picture: what it stands for, in the middle
            label = emoji_of(media) if sticker else "GIF"
            self._label(row, label if line == (rows - 1) // 2 else "", lit, selected)
            return
        if line == rows - 1:
            self._label(row, emoji_of(media) if sticker else "", lit, selected)
            return
        top, left = (rows - 1 - box.rows) // 2, (cols - box.cols) // 2
        if not top <= line < top + box.rows:
            row.add(" " * cols, cols, lit)
            return
        assert self._gallery
        blocks = self._gallery.blocks(media, box, self._background(selected))
        row.add(" " * left, left, lit)
        if blocks is None:
            row.add(" " * box.cols, box.cols, lit)
        else:
            for piece in blocks[line - top]:
                row.add(piece.text, piece.cell_length, lit + piece.style if piece.style else lit)
        right = cols - left - box.cols
        row.add(" " * right, right, lit)

    def _label(self, row: Row, text: str, lit: Style, selected: bool) -> None:
        cols = self._cell[0]
        shown, cells = self._options.fit(text, cols - 2) if text else ("", 0)
        before = (cols - cells) // 2
        style = lit if selected else self.get_component_rich_style("grid--label")
        row.add(" " * before, before, lit).add(shown, cells, style)
        row.add(" " * (cols - before - cells), cols - before - cells, lit)

    def render_lines(self, crop) -> list[Strip]:
        strips = super().render_lines(crop)
        if self._gallery:
            self._layer.painted()
            if self._look_timer:
                self._look_timer.stop()
            self._look_timer = self.set_timer(LOOK, self._look)
        return strips

    def _in_view(self) -> list[tuple[int, int, int]]:
        """Index, column and row of each cell that is wholly in view."""
        found = []
        for shown in range(self._rows_shown):
            for column in range(self._columns):
                i = (self._first + shown) * self._columns + column
                if i < len(self.items):
                    found.append((i, column, shown))
        return found

    def _look(self) -> None:
        if self._gallery:
            self._gallery.want_files([self.items[i] for i, _, _ in self._in_view()])

    def _spots(self) -> list[Spot]:
        gallery = self._gallery
        if not gallery or not gallery.graphics.sixel:
            return []
        cols, rows = self._cell
        spots = []
        for i, column, shown in self._in_view():
            media = self.items[i]
            box, image = self._box(media), gallery.sharp(media)
            if box is None or image is None:
                continue
            background = self._background(i == self.index)
            top, left = (rows - 1 - box.rows) // 2, (cols - box.cols) // 2
            x = self.content_region.x + column * cols + left
            y = self.content_region.y + shown * rows + top
            key = (media.id, image.size, box, background)
            encode = partial(gallery.sixel, media, image, box, background, 0, box.rows)
            spots.append(Spot(key, x, y, encode))
        return spots

    def on_resize(self) -> None:
        self._keep_in_view()

    def on_click(self, event: events.Click) -> None:
        cols, rows = self._cell
        column, shown = (event.x - 1) // cols, event.y // rows
        i = (self._first + shown) * self._columns + column
        if 0 <= column < self._columns and shown < self._rows_shown and i < len(self.items):
            self.index = i
            self.refresh()
            self.post_message(self.Chosen())

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.move(0, rows=1)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.move(0, rows=-1)


class StickerPicker(ModalScreen[Media | None]):
    """Dismissed with the sticker or GIF to send."""

    def __init__(
        self,
        backend: Backend,
        gallery: Gallery | None,
        options: TextOptions,
        keys: dict[str, str],
        gifs: bool = False,
        memory: dict[str, str] | None = None,
    ) -> None:
        """`memory` keeps the pack that was open last time."""
        super().__init__()
        self._backend = backend
        self._gallery = gallery
        self._options = options
        self._keys = keys
        self._gifs = gifs
        self._memory = memory if memory is not None else {}
        self._packs: list[StickerPack] = []
        self._pack = 0
        self._unwatch = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="frame"), Vertical(classes="finder"):
            with Horizontal(id="sticker-panes"):
                if not self._gifs:
                    yield PackList(self._options)
                yield StickerGrid(self._gallery, self._options, wide=self._gifs)
            yield Static("Loading...", markup=False, id="sticker-name")
            yield Static("Esc cancel", markup=False, classes="finder-hints")

    @property
    def grid(self) -> StickerGrid:
        return self.query_one(StickerGrid)

    def on_mount(self) -> None:
        self.query_one(".finder").border_title = "GIFs" if self._gifs else "Stickers"
        if self._gallery:
            self._unwatch = self._gallery.watch(self.grid.refresh)
        self.run_worker(self._load(), group="packs", exclusive=True)

    def on_unmount(self) -> None:
        if self._unwatch:
            self._unwatch()

    async def _load(self) -> None:
        try:
            if self._gifs:
                gifs = await self._backend.saved_gifs()
                packs = [StickerPack("gifs", "Saved GIFs", len(gifs), gifs)]
            else:
                packs = await self._backend.sticker_packs()
        except BackendError as error:
            self._say(error.message)
            return
        if not self.is_attached:
            return
        self._packs = [p for p in packs if p.stickers or p.count]
        if not self._packs:
            self._say(NO_GIFS if self._gifs else NO_STICKERS)
            return
        move = keyboard.hints(self._keys, ("focus_list down up focus_chat", "move"))
        packs = "" if self._gifs else "  Tab pack"
        self.query_one(".finder-hints", Static).update(
            f"{move}{packs}  Enter send  Esc cancel".strip()
        )
        last = self._memory.get("gifs" if self._gifs else "stickers")
        self._open(next((i for i, p in enumerate(self._packs) if p.key == last), 0))

    def _open(self, index: int) -> None:
        if not self._packs:
            return
        self._pack = index % len(self._packs)
        pack = self._packs[self._pack]
        self._memory["gifs" if self._gifs else "stickers"] = pack.key
        if not self._gifs:
            self.query_one(PackList).show(self._packs, self._pack)
        self.grid.show(pack.stickers)
        if pack.stickers or not pack.count:
            self._describe()
        else:
            self._say("Loading...")
            self.run_worker(self._load_pack(self._pack), group="pack", exclusive=True)

    async def _load_pack(self, index: int) -> None:
        pack = self._packs[index]
        try:
            stickers = await self._backend.stickers(pack.key)
        except BackendError as error:
            if self._pack == index:
                self._say(error.message)
            return
        self._packs[index] = replace(pack, stickers=stickers)
        if self._pack == index and self.is_attached:
            self.grid.show(stickers)
            self._describe()

    def _say(self, text: str) -> None:
        if self.is_attached:
            shown = self._options.fit(text, max(1, self.size.width))[0]
            self.query_one("#sticker-name", Static).update(shown)

    @on(StickerGrid.Moved)
    def _describe(self) -> None:
        grid = self.grid
        media = grid.selected
        if media is None:
            self._say("This pack is empty.")
            return
        place = f"{grid.index + 1} of {len(grid.items)}"
        if self._gifs:
            self._say(f"GIF {place}")
        else:
            # The title last: it may be written right to left.
            self._say(f"{emoji_of(media)}  {place}  {self._packs[self._pack].title}")

    @on(PackList.Clicked)
    def _pack_clicked(self, event: PackList.Clicked) -> None:
        self._open(event.index)

    @on(StickerGrid.Chosen)
    def _choose(self) -> None:
        media = self.grid.selected
        if media is not None:
            self.dismiss(media)

    def on_key(self, event: events.Key) -> None:
        grid = self.grid
        key = pressed(event)
        event.stop()
        event.prevent_default()
        if key in ("escape", "q"):
            self.dismiss(None)
        elif key in ("enter", " "):
            self._choose()
        elif key == "tab":
            self._open(self._pack + 1)
        elif key == "shift+tab":
            self._open(self._pack - 1)
        else:
            match action_for(event, self._keys):
                case "focus_list":
                    grid.move(-1)
                case "focus_chat":
                    grid.move(1)
                case "down":
                    grid.move(0, rows=1)
                case "up":
                    grid.move(0, rows=-1)
                case "page_down":
                    grid.page(1)
                case "page_up":
                    grid.page(-1)
                case "next_tab":
                    self._open(self._pack + 1)
                case "previous_tab":
                    self._open(self._pack - 1)
                case "stickers" | "gifs" | "deselect":
                    self.dismiss(None)
                case "open":
                    self._choose()
