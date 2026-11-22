"""The colours of the terminal, as the terminal itself reports them."""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)
RGB = tuple[int, int, int]

_REPLY = re.compile(r"\x1b\](?:4;(\d+)|(10|11));rgb:([0-9a-f/]+)", re.IGNORECASE)
_QUERY = "\x1b]10;?\x1b\\\x1b]11;?\x1b\\" + "".join(f"\x1b]4;{i};?\x1b\\" for i in range(16))


@dataclass(frozen=True, slots=True)
class Palette:
    foreground: RGB
    background: RGB | None  # Windows Terminal does not say while it is see-through
    colours: tuple[RGB, ...]  # 16 of them

    @property
    def dark(self) -> bool:
        if self.background is not None:
            return luminance(self.background) < luminance(self.foreground)
        return luminance(self.foreground) > 0.2

    @property
    def base(self) -> RGB:
        """The background, or what it most likely is."""
        if self.background is not None:
            return self.background
        return min(self.colours, key=luminance) if self.dark else max(self.colours, key=luminance)


def query() -> Palette | None:
    """Asks the terminal, so only before the app takes over the console."""
    try:
        from textual_image._terminal import capture_until_primary_da

        if not sys.__stdout__ or not sys.__stdout__.isatty():
            return None
        with capture_until_primary_da(1.0) as reply:
            sys.__stdout__.write(f"{_QUERY}\x1b[c")  # everything answers the last one
            sys.__stdout__.flush()
        return parse(reply.sequence)
    except Exception as error:
        log.info("terminal colours unknown: %s", type(error).__name__)
        return None


def parse(sequence: str) -> Palette | None:
    found: dict[int, RGB] = {}
    special: dict[str, RGB] = {}
    for index, name, value in _REPLY.findall(sequence):
        colour = _colour(value)
        if colour and index:
            found[int(index)] = colour
        elif colour:
            special[name] = colour
    if "10" not in special or any(i not in found for i in range(16)):
        return None
    return Palette(special["10"], special.get("11"), tuple(found[i] for i in range(16)))


def _colour(value: str) -> RGB | None:
    """"rgb:" values have one to four hexadecimal digits each."""
    parts = value.split("/")
    if len(parts) != 3 or not all(0 < len(part) <= 4 for part in parts):
        return None
    red, green, blue = (round(int(p, 16) * 255 / (16 ** len(p) - 1)) for p in parts)
    return red, green, blue


def luminance(colour: RGB) -> float:
    def linear(value: int) -> float:
        part = value / 255
        return part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4

    red, green, blue = map(linear, colour)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(one: RGB, other: RGB) -> float:
    low, high = sorted((luminance(one), luminance(other)))
    return (high + 0.05) / (low + 0.05)


def mix(one: RGB, other: RGB, part: float) -> RGB:
    """`one` with `part` of `other`."""
    red, green, blue = (round(a + (b - a) * part) for a, b in zip(one, other, strict=True))
    return red, green, blue


def readable(colour: RGB, background: RGB, least: float = 4.5) -> RGB:
    """The colour, or the nearest to it that can be read on this background."""
    goal = (255, 255, 255) if luminance(background) < 0.18 else (0, 0, 0)
    for step in range(11):
        candidate = mix(colour, goal, step / 10)
        if contrast(candidate, background) >= least:
            return candidate
    return goal
