"""How pictures can be shown in this terminal."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Graphics:
    on: bool = True
    sixel: bool = False
    cell: tuple[int, int] = (10, 20)  # pixels per character cell
    # Two pixels per cell. Windows Terminal can be set to adjust text colours that are
    # close to their background, which ruins that, so there it is one.
    half: bool = True


def detect(setting: str) -> Graphics:
    """Asks the terminal, so only before the app takes over the console."""
    if setting == "off":
        return Graphics(on=False)
    half = "WT_SESSION" not in os.environ
    if setting == "blocks":
        return Graphics(half=half)
    try:
        from textual_image._sixel import image_to_sixels  # noqa: F401
        from textual_image._terminal import probe_terminal

        found = probe_terminal()
    except Exception as error:
        log.info("no terminal graphics: %s", type(error).__name__)
        return Graphics(half=half)
    width, height = found.cell_size
    sixel = found.sixel or setting == "sixel"
    return Graphics(sixel=sixel, cell=(width, height), half=half and not sixel)
