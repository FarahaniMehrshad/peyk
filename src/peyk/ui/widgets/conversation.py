"""One chat's messages.

Drawn line by line and measured from the bottom: `lines_up` is how many lines we
are scrolled up, so loading older messages above never moves what is on screen.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import timedelta
from functools import partial

from rich.style import Style
from textual import events
from textual.geometry import Region
from textual.message import Message as UiMessage
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget

from peyk.images import Backdrop, Box
from peyk.models import Chat, Kind, Media, Message, State
from peyk.outbox import FIRST_ID
from peyk.store import History
from peyk.text import format as fmt
from peyk.text.bidi import VisualLine, layout
from peyk.ui.gallery import NOTHING, Gallery
from peyk.ui.widgets.sixels import SixelLayer, Spot
from peyk.ui.widgets.strips import Row, TextOptions, backdrop, ink

GUTTER = 2
SIDE = 10  # time and ticks on the right
GROUP_GAP = timedelta(minutes=5)
SENDER_COLOURS = 6
MAX_PICTURE_COLS = 36
MAX_PICTURE_ROWS = 12
LOOK = 0.15  # seconds of stillness before pictures in view are fetched


@dataclass(slots=True)
class Block:
    """A run of lines: a message, or a separator (id 0)."""

    id: int
    lines: list[Strip]
    start: int = 0  # line number from the top
    picture: Place | None = None


@dataclass(frozen=True, slots=True)
class Place:
    """Where a message's picture is, within its lines."""

    message: Message
    media: Media
    box: Box
    line: int
    hidden: bool  # behind a spoiler
    background: Backdrop


class Conversation(Widget):
    COMPONENT_CLASSES = {
        "msg--selected", "msg--bar", "msg--dim", "msg--me", "msg--reply", "msg--media",
        "msg--service", "msg--separator", "msg--unread", "msg--ticks",
        "msg--trouble", "msg--code", "msg--link", "msg--mention", "msg--spoiler", "msg--quote",
        *(f"msg--sender-{i}" for i in range(SENDER_COLOURS)),
    }  # fmt: skip
    DEFAULT_CSS = """
    Conversation { height: 1fr; }
    Conversation > .msg--selected { background: $surface; }
    Conversation > .msg--bar { color: $primary; }
    Conversation > .msg--dim { color: $text-muted; }
    Conversation > .msg--me { color: $primary; text-style: bold; }
    Conversation > .msg--reply { color: $secondary; }
    Conversation > .msg--media { color: $accent; }
    Conversation > .msg--service { color: $text-muted; text-style: italic; }
    Conversation > .msg--separator { color: $text-muted; }
    Conversation > .msg--unread { color: $warning; }
    Conversation > .msg--ticks { color: $success; }
    Conversation > .msg--trouble { color: $error; }
    Conversation > .msg--code { color: $warning; }
    Conversation > .msg--link { color: $accent; text-style: underline; }
    Conversation > .msg--mention { color: $accent; }
    Conversation > .msg--spoiler { color: $text-muted; }
    Conversation > .msg--quote { color: $text-muted; text-style: italic; }
    Conversation > .msg--sender-0 { color: $sender-0; text-style: bold; }
    Conversation > .msg--sender-1 { color: $sender-1; text-style: bold; }
    Conversation > .msg--sender-2 { color: $sender-2; text-style: bold; }
    Conversation > .msg--sender-3 { color: $sender-3; text-style: bold; }
    Conversation > .msg--sender-4 { color: $sender-4; text-style: bold; }
    Conversation > .msg--sender-5 { color: $sender-5; text-style: bold; }
    """

    class NeedOlder(UiMessage):
        def __init__(self, chat_id: int) -> None:
            super().__init__()
            self.chat_id = chat_id

    class NeedNewer(UiMessage):
        def __init__(self, chat_id: int) -> None:
            super().__init__()
            self.chat_id = chat_id

    class Moved(UiMessage):
        """What is visible may have changed."""

    class Looked(UiMessage):
        """These messages with pictures are in view."""

        def __init__(self, messages: list[Message]) -> None:
            super().__init__()
            self.messages = messages

    def __init__(
        self,
        options: TextOptions,
        id: str,  # noqa: A002
        gallery: Gallery | None = None,
    ) -> None:
        """Without a gallery, pictures are only named."""
        super().__init__(id=id)
        self._options = options
        self._gallery = gallery
        self._layer = SixelLayer(self, self._spots)
        self._look_timer: Timer | None = None
        self._shown: list[Strip] = []
        # (chat, message) -> how far its transfer is; kept by the screen
        self.progress: dict[tuple[int, int], str] = {}
        self.chat: Chat | None = None
        self.history: History | None = None
        self.pending: list[Message] = []  # not sent yet
        self.selected: int | None = None
        self.lines_up = 0
        self.note = ""
        self.unread_from = 0  # show the "unread" line before the first message above this id
        self._blocks: list[Block] = []
        self._starts: list[int] = []
        self._total = 0
        self._cache: dict[int, tuple] = {}
        self._style_key: object = None

    # -- content ------------------------------------------------------------

    def open(self, chat: Chat, history: History, pending: list[Message]) -> None:
        self.chat, self.history, self.pending = chat, history, pending
        self.selected, self.lines_up, self.note = None, 0, ""
        self.unread_from = chat.read_inbox if chat.unread else 0
        self._cache.clear()
        self.rebuild()
        if self.unread_from:
            self._show_unread()

    def close(self, note: str = "") -> None:
        self.chat = self.history = self.selected = None
        self.pending = []
        self.lines_up, self.note = 0, note
        self._cache.clear()
        self.rebuild()

    def set_note(self, note: str) -> None:
        if note != self.note:
            self.note = note
            self._repaint()

    def update_chat(self, chat: Chat) -> None:
        self.chat = chat

    def content_changed(self) -> None:
        """Messages came, went or changed: keep the same lines on screen."""
        anchor = self._anchor() if self.lines_up else None
        self.rebuild()
        if anchor:
            self._restore(anchor)
        self._clamp()
        self._repaint()
        self.post_message(self.Moved())

    def newer_loaded(self) -> None:
        anchor = self._anchor()
        self.rebuild()
        if anchor:
            self._restore(anchor)
        self._clamp()
        self._repaint()
        self.post_message(self.Moved())

    @property
    def messages(self) -> list[Message]:
        if self.history is None:
            return []
        return self.history.messages + (self.pending if self.history.at_end else [])

    @property
    def selected_message(self) -> Message | None:
        return next((m for m in self.messages if m.id == self.selected), None)

    def older_loaded(self) -> None:
        self.rebuild()
        self._clamp()
        self._repaint()
        self.post_message(self.Moved())

    def _anchor(self) -> tuple[int, int] | None:
        """(message id, how many lines its first line is above the bottom edge)."""
        bottom = self._total - self.lines_up
        for block in reversed(self._blocks):
            if block.id and block.start < bottom:
                return block.id, bottom - block.start
        return None

    def _restore(self, anchor: tuple[int, int]) -> None:
        message_id, above = anchor
        for block in self._blocks:
            if block.id >= message_id:
                self.lines_up = self._total - (block.start + above)
                return

    def rebuild(self) -> None:
        self._blocks, self._starts, self._total = [], [], 0
        width = self.size.width
        if not self.history or not self.chat or width <= GUTTER + SIDE:
            self._repaint()
            return
        style_key = (
            width,
            self._options.mode,
            self._options.lam_alef,
            self.app.theme,
            self._picture_rows,
        )
        if style_key != self._style_key:
            self._cache.clear()
            self._style_key = style_key
        previous: Message | None = None
        marked = False
        for message in self.messages:
            day = message.date.astimezone().date()
            if previous is None or previous.date.astimezone().date() != day:
                self._push(Block(0, [self._separator(fmt.day_heading(day), "separator")]))
                previous = None
            if self.unread_from and not marked and message.id > self.unread_from:
                if not message.outgoing:
                    marked = True
                    self._push(Block(0, [self._separator("Unread", "unread")]))
                    previous = None
            header = self._needs_header(message, previous)
            lines, place = self._message_lines(message, header, previous)
            self._push(Block(message.id, lines, picture=place))
            previous = message
        if self.selected is not None and self._block(self.selected) is None:
            self.selected = None
        self._repaint()

    def _push(self, block: Block) -> None:
        block.start = self._total
        self._blocks.append(block)
        self._starts.append(block.start)
        self._total += len(block.lines)

    def _needs_header(self, message: Message, previous: Message | None) -> bool:
        return (
            previous is None
            or previous.service
            or previous.sender_id != message.sender_id
            or previous.sender != message.sender
            or previous.outgoing != message.outgoing
            or message.date - previous.date > GROUP_GAP
        )

    # -- drawing one message ------------------------------------------------

    def _style(self, name: str) -> Style:
        return ink(self, f"msg--{name}")

    def _separator(self, text: str, style: str) -> Strip:
        width = self.size.width
        label = f" {text} "
        side = max(0, (width - len(label)) // 2)
        row = Row(width, self.rich_style)
        line = "─" * side + label + "─" * max(0, width - side - len(label))
        return row.add(line[:width], min(width, len(line)), self._style(style)).strip()

    def _message_lines(
        self, message: Message, header: bool, previous: Message | None
    ) -> tuple[list[Strip], Place | None]:
        assert self.chat
        selected = message.id == self.selected
        read = message.state is State.SENT and message.id <= self.chat.read_outbox
        progress = self.progress.get((message.chat_id, message.id), "")
        shown = NOTHING
        if self._gallery and message.media and message.media.picture:
            shown = self._gallery.state(message.media)
        key = (message, header, previous is not None, selected, read, progress, shown)
        cached = self._cache.get(message.id)
        if cached and cached[0][0] is message and cached[0][1:] == key[1:]:
            return cached[1]
        drawn = self._draw(message, header, previous is not None, selected, read, progress)
        self._cache[message.id] = (key, drawn)
        return drawn

    @property
    def _picture_rows(self) -> int:
        """Of the screen, not of this widget, which shrinks while a message is written."""
        return max(3, min(MAX_PICTURE_ROWS, self.app.size.height // 3))

    def _draw(
        self,
        message: Message,
        header: bool,
        gap: bool,
        selected: bool,
        read: bool,
        progress: str,
    ) -> tuple[list[Strip], Place | None]:
        assert self.chat
        width = self.size.width
        base = self.rich_style
        if selected:
            base += Style(bgcolor=self.get_component_rich_style("msg--selected").bgcolor)
        room = width - GUTTER - SIDE
        lines: list[Strip] = []

        def start() -> Row:
            row = Row(width, base)
            row.add("▌" if selected else " ", 1, self._style("bar"))
            return row.space(1)

        if message.service:
            text, cells = self._options.fit(message.text, width - GUTTER * 2)
            row = start().space(max(0, (width - GUTTER * 2 - cells) // 2))
            return [row.add(text, cells, self._style("service")).strip()], None

        if header and gap:
            lines.append(Strip.blank(width, self.rich_style))
        if header and (message.outgoing or self.chat.kind is not Kind.CHANNEL):
            name, cells = self._options.fit("You" if message.outgoing else message.sender, room)
            style = "me" if message.outgoing else f"sender-{message.sender_id % SENDER_COLOURS}"
            lines.append(start().add(name, cells, self._style(style)).strip())

        # Everything below the name, each as (text, width, right-to-left?, pieces).
        body: list[tuple[list[tuple[str, int, Style | None]], int, bool]] = []

        def simple(text: str, style: str, lead: str = "") -> None:
            shown, cells = self._options.fit(text, room - len(lead))
            pieces: list[tuple[str, int, Style | None]] = []
            if lead:
                pieces.append((lead, len(lead), self._style(style)))
            pieces.append((shown, cells, self._style(style)))
            body.append((pieces, len(lead) + cells, False))

        if message.forwarded:
            simple(f"Forwarded from {message.forwarded}", "dim")
        if message.reply:
            who = f"{message.reply.name}: " if message.reply.name else ""
            simple(f"{who}{message.reply.text}", "reply", "┃ ")
        picture_at, box = -1, None
        background = self._backdrop(base)
        media = message.media
        if media:
            label = f"[{media.label}]" + (f"  {progress}" if progress else "")
            simple(label, "media")
            if self._gallery:
                box = self._gallery.box(media, min(room, MAX_PICTURE_COLS), self._picture_rows)
        hidden = bool(media and media.spoiler and not selected)
        if media and box:
            picture_at = len(body)
            body.extend(self._picture_lines(media, box, hidden, background))
        if message.text:
            body.extend(self._text_lines(message, room, selected))
        if not body:
            simple("(empty message)", "dim")
        if message.trouble:
            simple(message.trouble, "trouble")
            if selected and message.state is State.FAILED:
                simple("Enter: try again    e: back to writing    d: discard", "dim")

        place: Place | None = None
        for i, (pieces, cells, rtl) in enumerate(body):
            row = start()
            if rtl:
                row.space(room - cells)
            for text, piece_cells, style in pieces:
                row.add(text, piece_cells, style)
            if i == 0:
                self._add_side(row, message, read)
            if i == picture_at and media and box:
                place = Place(message, media, box, len(lines), hidden, background)
            lines.append(row.strip())
        return lines, place

    def _backdrop(self, style: Style) -> Backdrop:
        if style.bgcolor is None or style.bgcolor.triplet is None:
            return backdrop(self)
        return tuple(style.bgcolor.triplet)

    def _picture_lines(self, media: Media, box: Box, hidden: bool, background: Backdrop):
        assert self._gallery
        blocks = None if hidden else self._gallery.blocks(media, box, background)
        if blocks is None:
            fill = ("▒" * box.cols, box.cols, self._style("spoiler"))
            return [([fill], box.cols, False)] * box.rows
        return [
            ([(s.text, s.cell_length, s.style) for s in row], box.cols, False) for row in blocks
        ]

    def _add_side(self, row: Row, message: Message, read: bool) -> None:
        ticks = ""
        if message.outgoing and self.chat and self.chat.kind is not Kind.SAVED:
            ticks = " ✓✓" if read else " ✓"
        style = "ticks"
        if message.state is not State.SENT:
            ticks, style = (" !", "trouble") if message.trouble else (" …", "dim")
        stamp = ("*" if message.edited else "") + fmt.clock(message.date)
        row.space(row.left - len(stamp) - 3)
        row.add(stamp, len(stamp), self._style("dim"))
        row.add(ticks, len(ticks), self._style(style))

    def _text_lines(self, message: Message, room: int, selected: bool):
        options = self._options
        lines = layout(message.text, room, options.mode, lam_alef=options.lam_alef)
        kinds: list[set[str]] | None = None
        if message.spans:
            kinds = [set() for _ in message.text]
            for span in message.spans:
                for i in range(span.start, min(span.end, len(kinds))):
                    kinds[i].add(span.kind)
        result = []
        for line in lines:
            result.append((self._pieces(line, kinds, selected), line.width, line.rtl))
        return result

    def _pieces(self, line: VisualLine, kinds: list[set[str]] | None, reveal: bool):
        if kinds is None:
            return [(line.text, line.width, None)]
        pieces: list[tuple[str, int, Style | None]] = []
        current: frozenset[str] | None = None
        text, cells = "", 0
        for cell in line.cells:
            kind = frozenset(kinds[cell.start]) if cell.start < len(kinds) else frozenset()
            glyph = cell.text
            if "spoiler" in kind and not reveal and cell.width and not cell.space:
                glyph = "▒" * cell.width
            if kind != current and text:
                pieces.append((text, cells, self._span_style(current)))
                text, cells = "", 0
            current = kind
            text += glyph
            cells += cell.width
        if text:
            pieces.append((text, cells, self._span_style(current)))
        return pieces

    def _span_style(self, kinds: frozenset[str] | None) -> Style | None:
        if not kinds:
            return None
        style = Style(
            bold="bold" in kinds or None,
            italic="italic" in kinds or None,
            underline="underline" in kinds or None,
            strike="strike" in kinds or None,
        )
        for kind, name in (
            ("quote", "quote"), ("code", "code"), ("pre", "code"), ("tag", "mention"),
            ("mention", "mention"), ("link", "link"), ("spoiler", "spoiler"),
        ):  # fmt: skip
            if kind in kinds:
                style += self._style(name)
        return style

    # -- drawing the screen -------------------------------------------------

    def _repaint(self) -> None:
        """Only the lines that differ: writing over a picture would make it flash."""
        before, self._shown = self._shown, [self.render_line(y) for y in range(self.size.height)]
        same_place = len(before) == len(self._shown) and not self._layer.moved
        if not same_place or (before and before[0].cell_length != self.size.width):
            self.refresh()
            return
        changed = [y for y, line in enumerate(self._shown) if line != before[y]]
        if changed:
            self.refresh(*(Region(0, y, self.size.width, 1) for y in changed))

    def render_line(self, y: int) -> Strip:
        width, height = self.size.width, self.size.height
        if self.note:
            if y == height - 1 or (not self._blocks and y == 0):
                row = Row(width, self.rich_style)
                text, cells = self._options.fit(self.note, width - GUTTER)
                return row.space(GUTTER).add(text, cells, self._style("unread")).strip()
            y += 1 if self._blocks else 0
        line = self._total - self.lines_up - height + y
        if line < 0 or line >= self._total:
            return Strip.blank(width, self.rich_style)
        block = self._blocks[bisect_right(self._starts, line) - 1]
        return block.lines[line - block.start]

    def render_lines(self, crop) -> list[Strip]:
        strips = super().render_lines(crop)
        if self._gallery:
            self._layer.painted()
            if self._look_timer:
                self._look_timer.stop()
            self._look_timer = self.set_timer(LOOK, self._look)
        return strips

    def _look(self) -> None:
        places = [p for p, _ in self._places_in_view()]
        if places:
            self.post_message(self.Looked([p.message for p in places if not p.hidden]))

    def _places_in_view(self) -> list[tuple[Place, int]]:
        """With the row of the screen where the picture starts, perhaps above the first."""
        top, end = self._view
        shift = 1 if self.note and self._blocks else 0  # the note takes the last row
        found = []
        for block in self._blocks:
            if block.picture:
                first = block.start + block.picture.line
                if first < end and first + block.picture.box.rows > top:
                    found.append((block.picture, first - top - shift))
        return found

    def _spots(self) -> list[Spot]:
        gallery = self._gallery
        if not gallery or not gallery.graphics.sixel:
            return []
        rows = self.size.height - (1 if self.note and self._blocks else 0)
        spots = []
        for place, y in self._places_in_view():
            image = gallery.sharp(place.media)
            first, last = max(0, -y), min(place.box.rows, rows - y)
            if image is None or place.hidden or first >= last:
                continue
            background = place.background
            key = (place.media.id, image.size, place.box, first, last, background)
            x, y = self.region.x + GUTTER, self.region.y + y + first
            encode = partial(
                gallery.sixel, place.media, image, place.box, background, first, last
            )
            spots.append(Spot(key, x, y, encode))
        return spots

    def on_resize(self, event: events.Resize) -> None:
        anchor = self._anchor() if self.lines_up else None
        self.rebuild()
        if anchor:
            self._restore(anchor)
        self._clamp()
        self.post_message(self.Moved())

    # -- scrolling and selection --------------------------------------------

    @property
    def _max_lines_up(self) -> int:
        return max(0, self._total - self.size.height)

    def _clamp(self) -> None:
        self.lines_up = max(0, min(self.lines_up, self._max_lines_up))

    def _after_move(self) -> None:
        self._clamp()
        self._repaint()
        self.post_message(self.Moved())
        if (
            self.chat
            and self.history
            and self.history.loaded
            and not self.history.complete
            and self._max_lines_up - self.lines_up < self.size.height
        ):
            self.post_message(self.NeedOlder(self.chat.id))
        if (
            self.chat
            and self.history
            and self.history.loaded
            and not self.history.at_end
            and self.lines_up < self.size.height
        ):
            self.post_message(self.NeedNewer(self.chat.id))

    def show_message(self, message_id: int) -> bool:
        """Select a message and put it in the middle."""
        block = self._block(message_id)
        if block is None:
            return False
        self._select(message_id)
        block = self._block(message_id)
        assert block
        below = max(1, (self.size.height - len(block.lines)) // 2)
        self.lines_up = self._total - (block.start + len(block.lines)) - below
        self._after_move()
        return True

    def scroll_lines(self, lines: int) -> None:
        """Positive scrolls towards older messages."""
        self.lines_up += lines
        self._after_move()

    def go_to_newest(self) -> None:
        self._select(None)
        self.lines_up = 0
        self._after_move()

    def deselect(self) -> bool:
        had = self.selected is not None
        self._select(None)
        return had

    def _select(self, message_id: int | None) -> None:
        if message_id != self.selected:
            self.selected = message_id
            self.rebuild()

    def _block(self, message_id: int) -> Block | None:
        return next((b for b in self._blocks if b.id == message_id), None)

    @property
    def _view(self) -> tuple[int, int]:
        """Lines on screen: first, and one past the last."""
        end = self._total - self.lines_up
        return end - self.size.height, end

    def _visible_messages(self) -> list[Block]:
        top, end = self._view
        return [b for b in self._blocks if b.id and b.start < end and b.start + len(b.lines) > top]

    def move_selection(self, step: int) -> None:
        """Select the next (+1) or previous (-1) message, scrolling long ones first."""
        messages = [b for b in self._blocks if b.id]
        if not messages:
            return
        current = self._block(self.selected) if self.selected else None
        visible = self._visible_messages()
        if current is None or current not in visible:
            choice = (visible or messages)[-1 if step < 0 or self.lines_up == 0 else 0]
            self._select(choice.id)
            self._after_move()
            return
        top, end = self._view
        hidden_below = current.start + len(current.lines) - end
        hidden_above = top - current.start
        if step > 0 and hidden_below > 0:
            self.lines_up -= min(hidden_below, max(1, self.size.height - 2))
        elif step < 0 and hidden_above > 0:
            self.lines_up += min(hidden_above, max(1, self.size.height - 2))
        else:
            index = messages.index(current) + step
            if 0 <= index < len(messages):
                self._select(messages[index].id)
                self._reveal(messages[index].id, prefer_end=step < 0)
                return
            if index >= len(messages):
                self.lines_up = 0
        self._after_move()

    def _reveal(self, message_id: int, prefer_end: bool = False) -> None:
        block = self._block(message_id)
        if block:
            top, end = self._view
            height = self.size.height
            last = block.start + len(block.lines)
            if len(block.lines) >= height:
                self.lines_up = self._total - (last if prefer_end else block.start + height)
            elif block.start < top:
                self.lines_up = self._total - (block.start + height)
            elif last > end:
                self.lines_up = self._total - last
        self._after_move()

    def _show_unread(self) -> None:
        """Put the unread line near the top, if it is not on screen already."""
        for i, block in enumerate(self._blocks):
            if block.id == 0 and i + 1 < len(self._blocks):
                following = self._blocks[i + 1]
                if following.id > self.unread_from and block.start > 0:
                    above = min(3, self.size.height // 4)
                    self.lines_up = self._total - (block.start - above + self.size.height)
                    break
        self._clamp()
        self._repaint()

    def last_visible_id(self) -> int:
        """Newest message whose last line is on screen."""
        _, end = self._view
        for block in reversed(self._blocks):
            if block.id and block.id < FIRST_ID and block.start + len(block.lines) <= end:
                return block.id
        return 0

    # -- mouse

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.scroll_lines(-3)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.scroll_lines(3)

    def on_click(self, event: events.Click) -> None:
        line = self._view[0] + event.y
        if 0 <= line < self._total:
            block = self._blocks[bisect_right(self._starts, line) - 1]
            if block.id:
                self._select(block.id)

