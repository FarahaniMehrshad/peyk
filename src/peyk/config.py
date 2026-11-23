"""User configuration: a small TOML file, every setting optional."""

from __future__ import annotations

import json
import os
import re
import textwrap
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from peyk.keys import DEFAULT_KEYS, KeyProblem, resolve, resolve_maps

_ACTIONS = textwrap.fill(", ".join(DEFAULT_KEYS), 88, initial_indent="#   ",
                         subsequent_indent="#   ")  # fmt: skip
TEMPLATE = f"""\
# Peyk configuration. Delete any line to get its default back.

[network]
# Overrides the proxy entered on the setup screen. Examples:
#   proxy = "system"                        use the Windows proxy
#   proxy = "none"                          connect directly
#   proxy = "socks5://127.0.0.1:1080"
#   proxy = "http://user:password@host:8080"
#   proxy = "tg://proxy?server=...&port=...&secret=..."
# A password written here is stored in plain text; the setup screen encrypts it.

[text]
# reorder   Windows Terminal (default)
# shape     terminals that do not join Arabic letters
# terminal  terminals with full right-to-left support
# off       leave text alone
rtl = "reorder"
# How lam-alef is drawn: stretch (joined, one cell wider), split, or font
lam_alef = "stretch"
# Turn **bold**, __italic__, `code` and so on into formatting when sending
formatting = true

[appearance]
# terminal takes the colours of your terminal. Others: catppuccin-mocha, nord, gruvbox,
# tokyo-night, dracula, monokai, solarized-light, ...
theme = "terminal"

[images]
# auto, sixel, blocks or off
mode = "auto"
# Show pictures between the messages. Without it, only the preview key shows them
inline = true

[files]
# Where downloads go. Empty: the folder Peyk in your Downloads
folder = ""

[notifications]
# Windows notifications for new messages, except in muted chats
enabled = true
# Without it, they say who wrote but not what
show_content = true

[privacy]
# Let others see when you are typing
typing = true

[keys]
# action = "key", as printed on a US keyboard, for example:
#   react = "-"
#   find_chat = "ctrl+f"
#   logout = ""            no key
# The actions are:
{_ACTIONS}

[maps]
# Letters that, typed quickly in a row, press a key instead, like vim's inoremap:
#   jj = "esc"             stop writing
#   jk = "enter"           send
# They work in every text box but the command bar. Typed slowly (a second apart), they stay
# letters. Inside Peyk, :map jj esc and :unmap jj change them here.
"""


class ConfigError(Exception):
    """The file exists but cannot be used; the message says why."""


@dataclass(frozen=True, slots=True)
class Config:
    proxy: str = ""  # empty: use the one from the setup screen
    rtl: str = "reorder"
    lam_alef: str = "stretch"
    formatting: bool = True
    theme: str = "terminal"
    images: str = "auto"
    inline_images: bool = True
    folder: str = ""
    notifications: bool = True
    show_content: bool = True
    show_typing: bool = True
    keys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_KEYS))
    maps: dict[str, str] = field(default_factory=dict)  # typed letters -> key


_CHOICES = {
    "rtl": ("reorder", "shape", "terminal", "off"),
    "lam_alef": ("stretch", "split", "font"),
    "images": ("auto", "sixel", "blocks", "off"),
}
# (section, key) -> (Config field, type)
_FIELDS: dict[tuple[str, str], tuple[str, type]] = {
    ("network", "proxy"): ("proxy", str),
    ("text", "rtl"): ("rtl", str),
    ("text", "lam_alef"): ("lam_alef", str),
    ("text", "formatting"): ("formatting", bool),
    ("appearance", "theme"): ("theme", str),
    ("images", "mode"): ("images", str),
    ("images", "inline"): ("inline_images", bool),
    ("files", "folder"): ("folder", str),
    ("notifications", "enabled"): ("notifications", bool),
    ("notifications", "show_content"): ("show_content", bool),
    ("privacy", "typing"): ("show_typing", bool),
}


def parse(text: str) -> Config:
    try:
        # Notepad likes to prepend a byte order mark.
        document = tomllib.loads(text.removeprefix("\N{ZERO WIDTH NO-BREAK SPACE}"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"not valid TOML: {error}") from error

    values: dict[str, Any] = {}
    for section, table in document.items():
        if not isinstance(table, dict):
            raise ConfigError(f"'{section}' must be inside a section such as [text]")
        if section in ("keys", "maps"):
            bad = [k for k, v in table.items() if not isinstance(v, str)]
            if bad:
                raise ConfigError(f"[{section}] {bad[0]} must be a string")
            try:
                values[section] = (resolve if section == "keys" else resolve_maps)(table)
            except KeyProblem as problem:
                raise ConfigError(f"[{section}] {problem}") from problem
            continue
        for key, value in table.items():
            if (section, key) not in _FIELDS:
                raise ConfigError(f"unknown setting [{section}] {key}")
            name, kind = _FIELDS[section, key]
            if type(value) is not kind:
                wanted = "true or false" if kind is bool else "a string"
                raise ConfigError(f"[{section}] {key} must be {wanted}")
            if name in _CHOICES and value not in _CHOICES[name]:
                options = ", ".join(_CHOICES[name])
                raise ConfigError(f"[{section}] {key} must be one of: {options}")
            values[name] = value
    return Config(**values)


_HEADER = re.compile(r"\s*\[([^\]]+)\]\s*(#.*)?")


def save_setting(path: Path, section: str, key: str, value: bool) -> None:
    """Changes one line of config.toml; everything else stays as it was written."""
    literal = "true" if value else "false"
    setting = re.compile(rf"(\s*{re.escape(key)}\s*=\s*)(true|false)(\s*(#.*)?)")

    def change(body: str) -> tuple[bool, str | None]:
        found = setting.fullmatch(body)
        return (True, found.group(1) + literal + found.group(3)) if found else (False, None)

    field_name = _FIELDS[section, key][0]
    _rewrite(path, section, key, change, f"{key} = {literal}",
             lambda read: getattr(read, field_name) == value)  # fmt: skip


def save_map(path: Path, typed: str, key: str | None) -> None:
    """Writes `typed = "key"` under [maps], or removes it when `key` is None."""
    line = f"{_toml_key(typed)} = \"{key}\""

    def change(body: str) -> tuple[bool, str | None]:
        found = _setting_in(body)
        if found is None or found[0] != typed:
            return False, None
        if key is None:
            return True, None
        indent = body[: len(body) - len(body.lstrip())]
        return True, indent + line + found[1]

    _rewrite(path, "maps", typed, change, line if key else None,
             lambda read: read.maps.get(typed) == key)  # fmt: skip


def _toml_key(text: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", text):
        return text
    return json.dumps(text, ensure_ascii=False)  # also a TOML string


def _setting_in(body: str) -> tuple[str, str] | None:
    """The key of a `key = value` line, and what follows the value (a comment)."""
    try:
        whole = tomllib.loads(body)
    except tomllib.TOMLDecodeError:
        return None
    if len(whole) != 1 or isinstance(next(iter(whole.values())), dict):
        return None
    key = next(iter(whole))
    for i, char in enumerate(body):
        if char != "#":
            continue
        try:
            if tomllib.loads(body[:i]) == whole:
                value_end = len(body[:i].rstrip())
                return key, body[value_end:]
        except tomllib.TOMLDecodeError:
            pass
    return key, ""


def _rewrite(
    path: Path,
    section: str,
    name: str,
    change: Callable[[str], tuple[bool, str | None]],
    addition: str | None,
    reads_as_meant: Callable[[Config], bool],
) -> None:
    """`change(line)`: whether it is the setting, and what it becomes (None: removed).
    `addition`: the line to add to the section when no line is the setting."""
    try:
        with open(path, encoding="utf-8", newline="") as file:  # line ends as they are
            text = file.read()
    except FileNotFoundError:
        text = TEMPLATE
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"could not be read: {error}") from error
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    current, header_at, changed = None, None, False
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        if found := _HEADER.fullmatch(body.lstrip("\N{ZERO WIDTH NO-BREAK SPACE}")):
            current = found.group(1).strip()
            if current == section and header_at is None:
                header_at = i
        elif current == section:
            ours, new_body = change(body)
            if ours:
                lines[i] = "" if new_body is None else new_body + ending
                changed = True
                break
    if changed or addition is None:
        pass
    elif header_at is not None:
        lines.insert(header_at + 1, f"{addition}{newline}")
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += newline
        lines.append(f"{newline}[{section}]{newline}{addition}{newline}")
    new = "".join(lines)
    # Only what still reads, and reads as meant.
    if not reads_as_meant(parse(new)):
        raise ConfigError(f"[{section}] {name} could not be changed")
    if new == text:
        return
    part = path.with_name(path.name + ".new")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(part, "w", encoding="utf-8", newline="") as out:
            out.write(new)
        os.replace(part, path)
    except OSError as error:
        part.unlink(missing_ok=True)
        raise ConfigError(f"could not be saved: {error}") from error


def load(path: Path, create: bool = True) -> Config:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(TEMPLATE, encoding="utf-8")
        return Config()
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"could not be read: {error}") from error
    return parse(text)
