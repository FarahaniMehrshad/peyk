"""Helpers for widgets that draw line by line."""

from __future__ import annotations

from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

from peyk.images import Backdrop
from peyk.text.bidi import LamAlef, Mode, fit


def ink(widget: Widget, name: str) -> Style:
    """A component's text style without its background.

    Theme colours such as $text-muted are transparent; only the full style has
    them blended, while a partial one would come out bright.
    """
    full = widget.get_component_rich_style(name)
    return Style(
        color=full.color,
        bold=full.bold,
        italic=full.italic,
        underline=full.underline,
        strike=full.strike,
    )


def backdrop(widget: Widget) -> Backdrop:
    """The colour behind a widget."""
    colour = widget.background_colors[1]
    return colour.rgb if colour.ansi is None and colour.a == 1 else None


class TextOptions:
    """How to draw right-to-left text, and what typed letters stand for; shared by all widgets."""

    __slots__ = ("lam_alef", "maps", "mode")

    def __init__(
        self,
        mode: Mode = "reorder",
        lam_alef: LamAlef = "stretch",
        maps: dict[str, str] | None = None,
    ) -> None:
        self.mode: Mode = mode
        self.lam_alef: LamAlef = lam_alef
        self.maps = dict(maps or {})  # letters typed in a row -> the key they press

    def fit(self, text: str, width: int) -> tuple[str, int]:
        """(text in visual order, its width), cut to `width` cells."""
        line = fit(text, width, self.mode, self.lam_alef)
        return line.text, line.width


class Row:
    """Collects the pieces of one screen line and tracks how wide it is."""

    def __init__(self, width: int, base: Style) -> None:
        self.width = width
        self.base = base
        self.used = 0
        self._segments: list[Segment] = []

    @property
    def left(self) -> int:
        return self.width - self.used

    def add(self, text: str, cells: int, style: Style | None = None) -> Row:
        if text and cells <= self.left:
            self._segments.append(Segment(text, self.base + style if style else self.base))
            self.used += cells
        return self

    def space(self, cells: int) -> Row:
        return self.add(" " * cells, cells) if cells > 0 else self

    def strip(self) -> Strip:
        self.space(self.left)
        return Strip(self._segments, self.width)
