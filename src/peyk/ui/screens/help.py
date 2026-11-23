"""All keys, on one page."""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from peyk.keys import hints, pretty
from peyk.ui.keymap import action_for, pressed
from peyk.ui.screens.command import COMMANDS

# (key or keys bound to an action, or fixed text) and what it does. An action name
# in braces is replaced by the key it is bound to.
SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Moving", (
        ("{down} {up}", "move in the list, or select a message"),
        ("{page_down} {page_up}", "ten chats, or scroll ten lines"),
        ("{newest}", "last chat, or newest message"),
        ("{focus_list} {focus_chat}", "go to the list, or to the conversation"),
        ("{open}", "open the chat, or deselect the message"),
        ("0-9", "folder (in the list) or tab (in a conversation)"),
    )),
    ("Writing", (
        ("{write}", "write a message"),
        ("{reply}", "reply to the selected message"),
        ("{edit}", "edit the selected message, or your last one"),
        ("{delete}", "delete the selected message"),
        ("{copy}", "copy the selected message"),
        ("{react}", "react to the selected message, or take it back"),
        ("{deselect}", "stop writing, then cancel the reply or edit"),
    )),
    ("Files", (
        ("{open_media}", "open the file or link; again stops the download"),
        ("{preview}", "show the picture"),
        ("{attach}", "send a file or photo"),
        ("{stickers} {gifs}", "send a sticker, a GIF"),
    )),
    ("While writing", (
        ("Enter", "send"),
        ("Ctrl+Enter, Ctrl+J", "new line"),
        ("Ctrl+Left, Ctrl+Right", "one word"),
        ("Ctrl+W", "delete a word"),
        ("Ctrl+U, Ctrl+K", "delete to the start, to the end of the line"),
        ("Ctrl+Z, Ctrl+Y", "undo, redo"),
        ("**bold** __italic__ ~~strike~~", ""),
        ("`code` ```block``` ||spoiler||", ""),
        ("[text](https://...)", "link"),
    )),
    ("Finding", (
        ("{find_chat}", "chats and contacts"),
        ("{find_message}", "messages"),
        ("{find_sender}", "people in this chat, and what they wrote"),
    )),
    ("Tabs and the rest", (
        ("{next_tab} {previous_tab}", "next, previous tab"),
        ("{close_tab} {close_others}", "close this tab, close the others"),
        ("{fullscreen}", "fullscreen"),
        ("{command}", "command bar"),
        ("{help}", "this page"),
        ("{logout} {quit}", "log out, quit"),
    )),
)  # fmt: skip


def help_text(keys: dict[str, str]) -> Text:
    text = Text()
    for title, rows in SECTIONS:
        text.append(f"{title}\n", "bold")
        for shown, meaning in rows:
            for action, key in keys.items():
                shown = shown.replace("{" + action + "}", pretty(key) or "-")
            text.append(f"  {shown:<24}", "cyan")
            text.append(f" {meaning}\n")
        text.append("\n")
    text.append("Commands\n", "bold")
    for command in COMMANDS:
        names = ", ".join(f":{name}" for name in (command.name, *command.aliases))
        text.append(f"  {names:<24}", "cyan")
        text.append(f" {command.help}\n")
    return text


class HelpScreen(ModalScreen[None]):
    def __init__(self, keys: dict[str, str]) -> None:
        super().__init__()
        self._keys = keys

    def compose(self) -> ComposeResult:
        with Vertical(classes="frame"), Vertical(classes="finder"):
            with VerticalScroll(id="help"):
                yield Static(help_text(self._keys))
            scroll = hints(self._keys, ("down up", "scroll"))
            yield Static(
                f"{scroll}  Esc close  Change keys in config.toml, under [keys]".strip(),
                markup=False,
                classes="finder-hints",
            )

    def on_mount(self) -> None:
        self.query_one(".finder").border_title = "Keys"

    def on_key(self, event: events.Key) -> None:
        page = self.query_one("#help", VerticalScroll)
        key = pressed(event)
        if key in ("escape", "q", "enter"):
            self.dismiss(None)
        elif key == " ":
            page.scroll_relative(y=10, animate=False)
        elif key == "home":
            page.scroll_home(animate=False)
        else:
            match action_for(event, self._keys):
                case "down":
                    page.scroll_relative(y=1, animate=False)
                case "up":
                    page.scroll_relative(y=-1, animate=False)
                case "page_down":
                    page.scroll_relative(y=10, animate=False)
                case "page_up":
                    page.scroll_relative(y=-10, animate=False)
                case "newest":
                    page.scroll_end(animate=False)
                case "help" | "quit" | "deselect":
                    self.dismiss(None)
                case _:
                    return
        event.stop()
        event.prevent_default()
