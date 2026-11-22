"""Downloaded files: where they go, what they may be called, what may be opened."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from unicodedata import category

from platformdirs import user_downloads_path

from peyk.backend.base import BackendError, ErrorKind

MAX_NAME = 120
_RESERVED = re.compile(r"(con|prn|aux|nul|conin\$|conout\$|com[0-9¹²³]|lpt[0-9¹²³])$", re.I)
_KEPT = "\N{ZERO WIDTH NON-JOINER}\N{ZERO WIDTH JOINER}"

# Types that only show something. Anything else is shown in its folder instead:
# a list of dangerous types would always miss one.
_OPENABLE = frozenset(
    (
        ".jpg .jpeg .png .gif .webp .bmp .heic .tif .tiff "
        ".mp4 .mkv .mov .avi .webm .m4v .3gp "
        ".mp3 .m4a .aac .ogg .oga .opus .flac .wav .wma "
        ".pdf .epub .txt .md .log .csv .json .srt "
        ".docx .xlsx .pptx .odt .ods .odp .rtf "
        ".zip .7z .rar"
    ).split()
)


def default_folder() -> Path:
    return user_downloads_path() / "Peyk"


def safe_name(name: str, fallback: str = "file") -> str:
    """A name that stays in its folder and looks like what it is.

    Names come from other people: no paths, no streams (":"), no device names, and
    no invisible characters that could make "gpj.exe" look like "exe.jpg".
    """
    name = name.replace("/", "\\").rsplit("\\", 1)[-1]
    name = "".join(c for c in name if c == " " or c in _KEPT or category(c)[0] not in "CZ")
    name = re.sub(r'[<>:"|?*]', "_", name).strip(" ").rstrip(". ").lstrip(".")
    stem, dot, suffix = name.rpartition(".")
    if not dot or len(suffix) > 12 or " " in suffix:
        stem, suffix = name, ""
    stem = stem.rstrip(". ")
    if not stem.strip(_KEPT + " "):
        stem = fallback
    if _RESERVED.match(stem.split(".")[0].strip()):
        stem = f"_{stem}"
    return stem[:MAX_NAME].rstrip(". ") + (f".{suffix}" if suffix else "")


def can_open(path: Path) -> bool:
    return path.suffix.lower() in _OPENABLE


def existing(folder: Path, name: str, size: int, sent: datetime) -> Path | None:
    """The same file, downloaded before: `finish` gives it the message's time."""
    for path in _numbered(folder, name):
        try:
            info = path.stat()
        except OSError:
            return None
        if size and info.st_size == size and abs(info.st_mtime - sent.timestamp()) < 2:
            return path
    return None


def finish(part: Path, name: str, sent: datetime) -> Path:
    """Give a complete download its name. Never replaces anything."""
    try:
        for path in _numbered(part.parent, name):
            try:
                os.rename(part, path)  # on Windows this fails if it exists
            except FileExistsError:
                continue
            _mark_as_downloaded(path)
            os.utime(path, (sent.timestamp(), sent.timestamp()))
            return path
    except OSError as error:
        raise disk_trouble("saved", error) from error
    raise AssertionError("unreachable")


def _numbered(folder: Path, name: str) -> Iterator[Path]:
    yield folder / name
    stem, suffix = os.path.splitext(name)
    number = 2
    while True:
        yield folder / f"{stem} ({number}){suffix}"
        number += 1


def _mark_as_downloaded(path: Path) -> None:
    """Windows then treats it like a file from the web: SmartScreen, Protected View."""
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii") as stream:
            stream.write("[ZoneTransfer]\nZoneId=3\n")
    except OSError:
        pass  # not NTFS


def disk_trouble(what: str, error: OSError) -> BackendError:
    """Trouble with the disk must not look like trouble with the network."""
    reason = error.strerror or type(error).__name__
    return BackendError(ErrorKind.DISK, f"The file cannot be {what}: {reason}.")


class Sink:
    """A file being downloaded."""

    def __init__(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file: BinaryIO = open(path, "wb")  # noqa: SIM115
        except OSError as error:
            raise disk_trouble("saved", error) from error

    def write(self, data: bytes) -> int:
        try:
            return self._file.write(data)
        except OSError as error:
            raise disk_trouble("saved", error) from error

    def flush(self) -> None:
        try:
            self._file.flush()
        except OSError as error:
            raise disk_trouble("saved", error) from error

    def tell(self) -> int:
        return self._file.tell()

    def seek(self, position: int) -> int:
        return self._file.seek(position)

    def truncate(self) -> int:
        try:
            return self._file.truncate()
        except OSError as error:
            raise disk_trouble("saved", error) from error

    def close(self) -> None:
        try:
            self._file.close()
        except OSError as error:
            raise disk_trouble("saved", error) from error


class Source:
    """A file being sent."""

    def __init__(self, path: Path) -> None:
        self.name = path.name
        try:
            self._file: BinaryIO = open(path, "rb")  # noqa: SIM115
            self.size = os.fstat(self._file.fileno()).st_size
        except OSError as error:
            raise disk_trouble("read", error) from error

    def read(self, size: int = -1) -> bytes:
        try:
            return self._file.read(size)
        except OSError as error:
            raise disk_trouble("read", error) from error

    def close(self) -> None:
        self._file.close()
