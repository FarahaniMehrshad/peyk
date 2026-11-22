"""Sharp pictures on top of the blocks that a widget draws.

Text is always drawn first, with blocks of colour where the picture goes. Once the
view is still, the picture is written over them, past Textual. Any text drawn there
later erases it, so nothing is ever left behind.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from textual.timer import Timer
from textual.widget import Widget

STILL = 0.08  # seconds without movement


@dataclass(frozen=True, slots=True)
class Spot:
    key: object  # what is drawn, so that the same spot twice is equal
    x: int  # on the screen
    y: int
    encode: Callable[[], Awaitable[str | None]] = field(compare=False)


class SixelLayer:
    def __init__(self, widget: Widget, find: Callable[[], list[Spot]]) -> None:
        """`find` says what is in view right now."""
        self._widget = widget
        self._find = find
        self._timer: Timer | None = None
        self._drawn: list[Spot] = []

    @property
    def moved(self) -> bool:
        """What is on the screen is not what belongs there."""
        return self._drawn != self._find()

    def painted(self) -> None:
        """The widget's text was just drawn, which may have erased pictures."""
        if self._timer:
            self._timer.stop()
        spots = self._find()
        if not spots:
            self._drawn = []
            return
        delay = 0.001 if spots == self._drawn else STILL
        self._timer = self._widget.set_timer(delay, self._start)

    def _start(self) -> None:
        self._widget.run_worker(self._draw(), group="sixels", exclusive=True)

    @property
    def _showing(self) -> bool:
        widget = self._widget
        return widget.is_attached and widget.screen is widget.app.screen and widget.size.width > 0

    async def _draw(self) -> None:
        if not self._showing:
            return
        spots = self._find()
        pictures = [(spot, await spot.encode()) for spot in spots]
        # Encoding takes time; if things moved meanwhile, `painted` starts over.
        if not self._showing or spots != self._find():
            return
        self._drawn = spots
        out = "".join(
            f"\x1b[{spot.y + 1};{spot.x + 1}H{data}" for spot, data in pictures if data
        )
        if out:
            write(self._widget, f"\x1b7{out}\x1b8")


def write(widget: Widget, data: str) -> None:
    driver = widget.app._driver
    if driver is not None and not driver.is_headless:
        driver.write(data)
