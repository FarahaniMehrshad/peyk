"""Choosing a reaction."""

from __future__ import annotations

from collections.abc import Sequence

from rich.style import Style
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.geometry import Size
from textual.message import Message as UiMessage
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Static

from peyk import keys as keyboard
from peyk.backend.base import Backend, BackendError
from peyk.models import Message, ReactionChoice, ReactionMenu
from peyk.text import format as fmt
from peyk.ui.keymap import action_for, pressed
from peyk.ui.widgets.bidi_label import BidiLabel
from peyk.ui.widgets.strips import Row, TextOptions, ink

CELL = 6  # digit, emoji, mark, gap
MAX_ROWS = 8
QUICK = 9  # the first ones have a digit


def toggled(mine: Sequence[str], key: str, most: int) -> tuple[str, ...]:
    """Our reactions after choosing one. A new one pushes out the oldest."""
    if key in mine:
        return tuple(k for k in mine if k != key)
    return (*mine, key)[-max(1, most) :]


class ReactionGrid(Widget):
    COMPONENT_CLASSES = {"grid--selected", "grid--digit", "grid--mine"}
    DEFAULT_CSS = """
    ReactionGrid { height: auto; margin: 1 0; }
    ReactionGrid > .grid--selected { color: $on-primary; background: $primary; }
    ReactionGrid > .grid--digit { color: $text-muted; }
    ReactionGrid > .grid--mine { color: $success; text-style: bold; }
    """

    class Moved(UiMessage):
        pass

    class Chosen(UiMessage):
        pass

    def __init__(self, options: TextOptions) -> None:
        super().__init__()
        self._options = options
        self.choices: tuple[ReactionChoice, ...] = ()
        self.mine: tuple[str, ...] = ()
        self.index = 0

    def show(self, choices: tuple[ReactionChoice, ...], mine: tuple[str, ...]) -> None:
        self.choices, self.mine = choices, mine
        self.index = next((i for i, c in enumerate(choices) if c.key in mine), 0)
        self.refresh(layout=True)

    @property
    def selected(self) -> ReactionChoice | None:
        return self.choices[self.index] if self.choices else None

    @property
    def _columns(self) -> int:
        return max(1, (self.size.width or CELL) // CELL)

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        rows = -(-len(self.choices) // max(1, width // CELL))
        return max(1, min(rows, MAX_ROWS, max(1, viewport.height - 12)))

    @property
    def _top(self) -> int:
        """The first row in view."""
        rows = -(-len(self.choices) // self._columns)
        row, shown = self.index // self._columns, max(1, self.size.height)
        return max(0, min(row - shown // 2, rows - shown))

    def move(self, step: int, rows: int = 0) -> None:
        target = self.index + step + rows * self._columns
        if rows and target >= len(self.choices):
            # Down from above a shorter last row lands on its end.
            last_row = (len(self.choices) - 1) // self._columns
            if self.index // self._columns < last_row:
                target = len(self.choices) - 1
        if 0 <= target < len(self.choices):
            self.index = target
            self.refresh()
            self.post_message(self.Moved())

    def render_line(self, y: int) -> Strip:
        row = Row(self.size.width, self.rich_style)
        first = (self._top + y) * self._columns
        for i in range(first, min(first + self._columns, len(self.choices))):
            choice = self.choices[i]
            lit = Style()
            if i == self.index:
                lit = self.get_component_rich_style("grid--selected")
            digit = str(i + 1) if i < QUICK else " "
            row.add(digit, 1, ink(self, "grid--digit") + lit)
            row.add(" ", 1, lit)
            label, cells = self._options.fit(choice.label, 2)
            row.add(label, cells, lit)
            row.add(" " * (2 - cells), 2 - cells, lit)
            mark = "•" if choice.key in self.mine else " "
            row.add(mark, 1, ink(self, "grid--mine") + lit)
            row.space(1)
        return row.strip()

    def on_click(self, event: events.Click) -> None:
        column, row = event.x // CELL, self._top + event.y
        index = row * self._columns + column
        if column < self._columns and 0 <= index < len(self.choices):
            self.index = index
            self.refresh()
            self.post_message(self.Chosen())


class ReactionPicker(ModalScreen[tuple[str, ...] | None]):
    """Dismissed with what our reactions to the message should be."""

    def __init__(
        self,
        backend: Backend,
        options: TextOptions,
        message: Message,
        keys: dict[str, str],
    ) -> None:
        super().__init__()
        self._backend = backend
        self._options = options
        self._message = message
        self._keys = keys
        self._menu = ReactionMenu()
        # Paid ones are not ours to give or take here.
        self._mine = tuple(r.key for r in message.reactions if r.mine and r.key != "paid")

    def compose(self) -> ComposeResult:
        options = self._options
        with Vertical(classes="frame small"), Vertical(classes="dialog reactions"):
            yield BidiLabel(fmt.body(self._message) or "(empty message)", mode=options.mode,
                            lam_alef=options.lam_alef, classes="quote first")  # fmt: skip
            yield ReactionGrid(options)
            yield Static("Loading...", markup=False, id="reaction-name")
            yield Static("Esc: cancel", markup=False, classes="hints")

    def on_mount(self) -> None:
        self.query_one(".dialog").border_title = "React"
        self.run_worker(self._load(), group="menu", exclusive=True)

    async def _load(self) -> None:
        try:
            self._menu = await self._backend.reaction_menu(self._message.chat_id)
        except BackendError as error:
            self._say(error.message)
            return
        if not self.is_attached:
            return
        if not self._menu.choices:
            self._say("Reactions are not allowed in this chat.")
            return
        self.query_one(ReactionGrid).show(self._menu.choices, self._mine)
        move = keyboard.hints(self._keys, ("focus_list down up focus_chat", "move"))
        self.query_one(".hints", Static).update(
            f"{move}  Enter choose  1-9 quick  Esc cancel".strip()
        )
        self._describe()

    def _say(self, text: str) -> None:
        if self.is_attached:
            self.query_one("#reaction-name", Static).update(text)

    @on(ReactionGrid.Moved)
    def _describe(self) -> None:
        choice = self.query_one(ReactionGrid).selected
        if choice:
            extra = "  (take back)" if choice.key in self._mine else ""
            self._say(f"{choice.name or 'Custom emoji'}{extra}")

    @on(ReactionGrid.Chosen)
    def _choose(self) -> None:
        choice = self.query_one(ReactionGrid).selected
        if choice:
            self.dismiss(toggled(self._mine, choice.key, self._menu.most))

    def on_key(self, event: events.Key) -> None:
        grid = self.query_one(ReactionGrid)
        key = pressed(event)
        event.stop()
        event.prevent_default()
        if key in ("escape", "q"):
            self.dismiss(None)
        elif key in ("enter", " "):
            self._choose()
        elif len(key) == 1 and key in "123456789":
            if int(key) <= len(grid.choices):
                grid.index = int(key) - 1
                self._choose()
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
                case "react" | "deselect":
                    self.dismiss(None)
                case "open":
                    self._choose()
