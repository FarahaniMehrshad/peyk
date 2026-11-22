"""Hand files and links to Windows."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from peyk.files import can_open


class CannotOpen(Exception):
    """The message is ready to show."""


def open_file(path: Path) -> None:
    """Only types that show something; `show` is for the rest."""
    if not can_open(path):
        raise CannotOpen("Peyk does not run programs.")
    _start(str(path), "Windows has no program for this kind of file.")


def show(path: Path) -> None:
    """Show a file in its folder, or a folder."""
    # Explorer wants exactly this quoting; names cannot contain quotes.
    command = f'explorer.exe "{path}"' if path.is_dir() else f'explorer.exe /select,"{path}"'
    try:
        subprocess.Popen(command)
    except OSError as error:
        raise CannotOpen("The folder could not be shown.") from error


def is_web_link(url: str) -> bool:
    try:
        parts = urlsplit(url)
        good = parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False
    return good and not any(c <= " " or c in '"<>' for c in url)


def open_link(url: str) -> None:
    if not is_web_link(url):
        raise CannotOpen("Only web links can be opened.")
    _start(url, "The link could not be opened.")


def _start(what: str, trouble: str) -> None:
    try:
        os.startfile(what)
    except OSError as error:
        raise CannotOpen(trouble) from error
