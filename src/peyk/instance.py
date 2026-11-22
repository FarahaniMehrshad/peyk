"""Allow one running Peyk per account: two would fight over the session file."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, "a+b")  # noqa: SIM115 - held for the process lifetime
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            handle.close()
            raise AlreadyRunning from error
        self._file = handle

    def release(self) -> None:
        if self._file:
            self._file.close()  # closing drops the lock
            self._file = None
