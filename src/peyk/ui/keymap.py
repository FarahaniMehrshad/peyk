"""Normal-mode keys, independent of the active keyboard layout."""

from __future__ import annotations

from textual.events import Key

from peyk.text.layout_keys import to_us_key

DEFAULT_KEYS: dict[str, str] = {
    "down": "j",
    "up": "k",
    "page_down": "J",
    "page_up": "K",
    "newest": "G",
    "focus_list": "h",
    "focus_chat": "l",
    "open": "enter",
    "next_tab": "tab",
    "previous_tab": "shift+tab",
    "close_tab": "x",
    "close_others": "X",
    "fullscreen": "f",
    "deselect": "escape",
    "write": "i",
    "reply": "r",
    "edit": "e",
    "delete": "d",
    "copy": "y",
    "open_media": "o",
    "preview": "p",
    "attach": "a",
    "find_chat": "/",
    "find_message": "?",
    "find_sender": "@",
    "command": ":",
    "help": "f1",
    "quit": "q",
    "logout": "L",
}

# Always available next to whatever is configured.
ALIASES: dict[str, str] = {
    "arrow_down": "down",
    "arrow_up": "up",
    "pagedown": "page_down",
    "pageup": "page_up",
    "end": "newest",
    "left": "focus_list",
    "right": "focus_chat",
}


def pressed(event: Key) -> str:
    """The key as labelled on a US keyboard: "j", "?", or a name like "enter"."""
    char = event.character
    if event.key.startswith(("alt+", "ctrl+")):
        return event.key
    if char and len(char) == 1 and char.isprintable():
        return to_us_key(char)
    return event.key


def action_for(event: Key, keys: dict[str, str]) -> str | None:
    key = pressed(event)
    for action, bound in keys.items():
        if bound == key:
            return action
    if key in ("down", "up"):  # arrow keys share their names with our actions
        return key
    return ALIASES.get(key)
