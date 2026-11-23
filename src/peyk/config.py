"""User configuration: a small TOML file, every setting optional."""

from __future__ import annotations

import textwrap
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from peyk.keys import DEFAULT_KEYS, KeyProblem, resolve

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
        if section == "keys":
            bad = [k for k, v in table.items() if not isinstance(v, str)]
            if bad:
                raise ConfigError(f"[keys] {bad[0]} must be a string")
            try:
                values["keys"] = resolve(table)
            except KeyProblem as problem:
                raise ConfigError(f"[keys] {problem}") from problem
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
