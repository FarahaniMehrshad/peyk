"""The command bar, like vim's."""

from __future__ import annotations

from dataclasses import dataclass

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from peyk.ui.widgets.edit_box import EditBox
from peyk.ui.widgets.strips import TextOptions


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    action: str  # a do_... method of the main screen
    help: str
    aliases: tuple[str, ...] = ()


COMMANDS = (
    Command("help", "help", "all keys", ("h",)),
    Command("quit", "quit", "leave Peyk", ("q",)),
    Command("saved", "saved", "open Saved Messages"),
    Command("read", "mark_read", "mark the chat as read"),
    Command("attach", "attach", "send a file"),
    Command("sticker", "stickers", "send a sticker"),
    Command("gif", "gifs", "send a GIF"),
    Command("downloads", "downloads", "show the folder with downloads"),
    Command("close", "close_tab", "close this tab", ("x",)),
    Command("only", "close_others", "close the other tabs"),
    Command("logout", "logout", "log out of Telegram"),
)


def find(typed: str) -> Command | None:
    """By name, alias, or the start of only one name."""
    typed = typed.strip().removeprefix(":").lower()
    for command in COMMANDS:
        if typed == command.name or typed in command.aliases:
            return command
    found = [c for c in COMMANDS if typed and c.name.startswith(typed)]
    return found[0] if len(found) == 1 else None


class CommandScreen(ModalScreen[str | None]):
    """Dismissed with the action to run."""

    def __init__(self, options: TextOptions) -> None:
        super().__init__()
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(classes="command"):
            yield Static("", id="command-help", markup=False)
            with Horizontal(classes="command-line"):
                yield Static(":", classes="command-mark", markup=False)
                yield EditBox(self._options, id="command")

    def on_mount(self) -> None:
        self.query_one("#command").focus()
        self._suggest()

    @property
    def _box(self) -> EditBox:
        return self.query_one(EditBox)

    def _matching(self) -> list[Command]:
        typed = self._box.text.strip().lower()
        return [c for c in COMMANDS if c.name.startswith(typed) or typed in c.aliases]

    def _suggest(self, trouble: str = "") -> None:
        found = self._matching()
        lines = [f"{c.name:<10} {c.help}" for c in found]
        self.query_one("#command-help", Static).update(trouble or "\n".join(lines))

    @on(EditBox.Changed)
    def _typed(self) -> None:
        self._suggest()

    @on(EditBox.Submitted)
    def _run(self) -> None:
        typed = self._box.text.strip()
        command = find(typed)
        if command:
            self.dismiss(command.action)
        elif typed:
            self._suggest(f"No command '{typed}'. Try help.")
        else:
            self.dismiss(None)

    @on(EditBox.Left)
    def _close(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "tab":
            event.stop()
            event.prevent_default()
            found = self._matching()
            if found:
                self._box.set(found[0].name)
                self._suggest()
        elif event.key == "escape":
            event.stop()
            self.dismiss(None)
