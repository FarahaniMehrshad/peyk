"""Normal-mode keys, independent of the active keyboard layout."""

from __future__ import annotations

from textual.events import Key

from peyk.keys import ALIASES
from peyk.text.layout_keys import to_us_key


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
