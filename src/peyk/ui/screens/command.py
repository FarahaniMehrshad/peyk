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
    choices: tuple[str, ...] = ()  # of what may follow it


def split(typed: str) -> tuple[str, str]:
    """The command's name and what follows it."""
    name, _, rest = typed.strip().partition(" ")
    return name, rest.strip().lower()


COMMANDS = (
    Command("help", "help", "all keys", ("h",)),
    Command("quit", "quit", "leave Peyk", ("q",)),
    Command("saved", "saved", "open Saved Messages"),
    Command("read", "mark_read", "mark the chat as read"),
    Command("attach", "attach", "send a file"),
    Command("forward", "forward", "forward the selected message"),
    Command("pin", "pin", "pin the selected message"),
    Command("unpin", "unpin", "unpin the selected or shown message"),
    Command("chat", "chat_menu", "mute, archive, pin or mark the chat"),
    Command("sticker", "stickers", "send a sticker"),
    Command("gif", "gifs", "send a GIF"),
    Command("voice", "voice", "record a voice message"),
    Command("downloads", "downloads", "show the folder with downloads"),
    Command("notifications", "notifications", "on or off, or alone to switch them",
            choices=("on", "off")),  # fmt: skip
    Command("close", "close_tab", "close this tab", ("x",)),
    Command("only", "close_others", "close the other tabs"),
    Command("logout", "logout", "log out of Telegram"),
)


def find(typed: str) -> Command | None:
    """By name, alias, or the start of only one name."""
    typed = split(typed.removeprefix(":"))[0].lower()
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
        typed = split(self._box.text)[0].lower()
        return [c for c in COMMANDS if c.name.startswith(typed) or typed in c.aliases]

    def _suggest(self, trouble: str = "") -> None:
        found = self._matching()
        lines = [f"{c.name:<14} {c.help}" for c in found]
        self.query_one("#command-help", Static).update(trouble or "\n".join(lines))

    @on(EditBox.Changed)
    def _typed(self) -> None:
        self._suggest()

    @on(EditBox.Submitted)
    def _run(self) -> None:
        typed = self._box.text.strip()
        command = find(typed)
        argument = split(typed)[1]
        if command and argument and argument not in command.choices:
            takes = " or ".join(command.choices) if command.choices else "nothing after it"
            self._suggest(f"{command.name} takes {takes}.")
        elif command:
            self.dismiss(f"{command.action} {argument}".strip())
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
