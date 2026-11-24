"""Which key does what, and how to write it down."""

from __future__ import annotations

from collections.abc import Mapping
from difflib import get_close_matches

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
    "react": "+",
    "forward": "F",
    "pinned": "P",
    "replied_to": "u",
    "back": "ctrl+o",
    "chat_menu": "c",
    "open_media": "o",
    "preview": "p",
    "attach": "a",
    "stickers": "s",
    "gifs": "g",
    "voice": "v",
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

_NAMED = frozenset({
    "enter", "escape", "tab", "backspace", "delete", "insert", "home", "end", "pageup",
    "pagedown", "up", "down", "left", "right", *(f"f{i}" for i in range(1, 13)),
})  # fmt: skip
_OTHER_NAMES = {
    "esc": "escape", "return": "enter", "del": "delete", "ins": "insert", "pgup": "pageup",
    "pgdn": "pagedown", "pgdown": "pagedown", "page_up": "pageup", "page_down": "pagedown",
    "plus": "+", "minus": "-", "comma": ",", "space": " ", "cr": "enter", "bs": "backspace",
}  # fmt: skip
_MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift"}
# Terminals send the same thing for these and for another key.
_SAME = {"ctrl+i": "tab", "ctrl+m": "enter", "ctrl+h": "backspace", "ctrl+[": "escape"}
_TAKEN = {
    "ctrl+q": "always quits",
    "ctrl+c": "belongs to the terminal",
    **{digit: "chooses a folder or tab" for digit in "0123456789"},
}


class KeyProblem(Exception):
    """The message is ready to show."""


def _parts(key: str) -> list[str]:
    """Modifiers, then the key itself, which may be a plus."""
    return [*key[:-2].split("+"), "+"] if key.endswith("++") else key.split("+")


def normal(written: str) -> str:
    """One way to write each key. Letters are those of a US keyboard."""
    if len(written) == 1:
        if not written.isascii() or not written.isprintable():
            raise KeyProblem(f"'{written}' is not on a US keyboard: use what is printed there")
        return written
    *modifiers, last = _parts(written.strip())
    held = {_MODIFIERS.get(m.strip().lower(), "") for m in modifiers}
    last = last.strip()
    if len(last) > 1:
        last = _OTHER_NAMES.get(last.lower(), last.lower())
    known = last in _NAMED or (len(last) == 1 and last.isascii() and last.isprintable())
    if not known or "" in held:
        raise KeyProblem(f"'{written}' is not a key")
    if len(last) == 1 and "shift" in held:
        if not last.isalpha():
            raise KeyProblem(f"'{written}': write the character that Shift gives instead")
        held.discard("shift")
        last = last.lower() if held else last.upper()
    elif len(last) == 1 and held:
        last = last.lower()
    return "+".join([m for m in ("ctrl", "alt", "shift") if m in held] + [last])


def resolve(chosen: Mapping[str, str]) -> dict[str, str]:
    """All keys, with the user's own. An empty one turns an action's key off."""
    keys = dict(DEFAULT_KEYS)
    for action, written in chosen.items():
        if action not in DEFAULT_KEYS:
            close = get_close_matches(action, DEFAULT_KEYS, n=1)
            hint = f"; did you mean {close[0]}?" if close else ""
            raise KeyProblem(f"there is no action '{action}'{hint}")
        keys[action] = _checked(action, written)
    owners: dict[str, str] = {}
    for action, key in keys.items():
        other = owners.setdefault(key, action) if key else action
        if other == action:
            continue
        if action in chosen and other in chosen:
            raise KeyProblem(f"{other} and {action} have the same key, {pretty(key)}")
        new, old = (action, other) if action in chosen else (other, action)
        raise KeyProblem(
            f"{new}: {pretty(key)} is already the key of {old}; give that another one"
        )
    return keys


def _checked(action: str, written: str) -> str:
    if not written.strip() and written != " ":
        return ""
    try:
        key = normal(written)
    except KeyProblem as problem:
        raise KeyProblem(f"{action}: {problem}") from None
    if key in _SAME:
        same = pretty(_SAME[key])
        raise KeyProblem(f"{action}: terminals cannot tell {pretty(key)} from {same}")
    if key in _TAKEN:
        raise KeyProblem(f"{action}: {pretty(key)} {_TAKEN[key]}")
    return key


# What typed letters can stand for in [maps]: the keys a text box knows.
TEXT_KEYS = frozenset({
    "escape", "enter", "ctrl+j", "ctrl+enter", "backspace", "delete", "left", "right", "up",
    "down", "home", "end", "ctrl+left", "ctrl+right", "ctrl+home", "ctrl+end", "ctrl+w",
    "ctrl+u", "ctrl+k", "ctrl+z", "ctrl+y",
})  # fmt: skip


MAP_LONGEST = 16


def resolve_maps(chosen: Mapping[str, str]) -> dict[str, str]:
    """Letters typed in a row -> the key they stand for, like vim's inoremap jj <Esc>."""
    maps: dict[str, str] = {}
    for typed, written in chosen.items():
        if not typed or not typed.isprintable():
            raise KeyProblem(f"'{typed}' cannot be typed")
        if len(typed) > MAP_LONGEST:
            raise KeyProblem(f"{typed}: at most {MAP_LONGEST} letters")
        try:
            key = normal(_from_vim(written)) if written.strip() else ""
        except KeyProblem as problem:
            raise KeyProblem(f"{typed}: {problem}") from None
        if key not in TEXT_KEYS:
            raise KeyProblem(f"{typed}: '{written}' is not a key for writing, such as esc or enter")
        maps[typed] = key
    for typed in maps:
        longer = next((t for t in maps if t != typed and t.startswith(typed)), None)
        if longer:
            raise KeyProblem(f"{typed} is the start of {longer}; one of them has to go")
    return maps


def _from_vim(written: str) -> str:
    """<Esc>, <CR>, <C-w> as vim writes them."""
    written = written.strip()
    if len(written) > 2 and written[0] == "<" and written[-1] == ">":
        written = written[1:-1]
    if written[:2].lower() == "c-" and len(written) > 2:
        written = "ctrl+" + written[2:]
    return written


def describe_maps(maps: Mapping[str, str]) -> str:
    """jj Esc, jk Enter"""
    return ", ".join(f"{typed} {pretty(key)}" for typed, key in maps.items())


def pretty(key: str) -> str:
    names = {" ": "Space", "escape": "Esc", "pageup": "PgUp", "pagedown": "PgDn"}
    *modifiers, last = _parts(key)
    if last not in names and modifiers:
        last = last.upper() if len(last) == 1 else last.capitalize()
    elif last not in names and len(last) > 1:
        last = last.capitalize()
    return "+".join([*(m.capitalize() for m in modifiers), names.get(last, last)])


def hints(keys: Mapping[str, str], *parts: tuple[str, str]) -> str:
    """("down up", "move") becomes "j/k move". What has no key is left out."""
    found = []
    for actions, meaning in parts:
        named = [pretty(keys[a]) for a in actions.split() if keys[a]]
        if named:
            found.append(f"{'/'.join(named)} {meaning}")
    return "  ".join(found)
