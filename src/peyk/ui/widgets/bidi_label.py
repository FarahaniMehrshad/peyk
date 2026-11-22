"""Static text that may be Persian, Arabic or Hebrew."""

from __future__ import annotations

from rich.text import Text
from textual.geometry import Size
from textual.widget import Widget

from peyk.text.bidi import LamAlef, Mode, layout
from peyk.text.sanitize import sanitize


class BidiLabel(Widget):
    """Plain text only: markup in the text is shown, never interpreted."""

    DEFAULT_CSS = "BidiLabel { width: 1fr; height: auto; }"

    def __init__(
        self,
        text: str = "",
        *,
        mode: Mode = "reorder",
        lam_alef: LamAlef = "stretch",
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._text = sanitize(text)
        self._mode: Mode = mode
        self._lam_alef: LamAlef = lam_alef

    @property
    def text(self) -> str:
        return self._text

    def update(self, text: str) -> None:
        clean = sanitize(text)
        if clean != self._text:
            self._text = clean
            self.refresh(layout=True)

    def _lines(self, width: int):
        return layout(self._text, width, self._mode, lam_alef=self._lam_alef)

    def get_content_width(self, container: Size, viewport: Size) -> int:
        return max((line.width for line in self._lines(max(1, container.width))), default=0)

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self._lines(width)) if self._text else 0

    def render(self) -> Text:
        width = self.content_size.width
        out = Text(no_wrap=True, end="")
        for i, line in enumerate(self._lines(width)):
            if i:
                out.append("\n")
            if line.rtl:
                out.append(" " * max(0, width - line.width))
            out.append(line.text)
        return out
