"""Questions. Anything but an explicit answer means no."""

from __future__ import annotations

from dataclasses import dataclass

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from peyk.ui.keymap import pressed
from peyk.ui.widgets.bidi_label import BidiLabel
from peyk.ui.widgets.strips import TextOptions


@dataclass(frozen=True, slots=True)
class Answer:
    key: str
    label: str
    value: str


class ChoiceScreen(ModalScreen[str | None]):
    def __init__(
        self,
        question: str,
        answers: list[Answer],
        quote: str = "",
        options: TextOptions | None = None,
        stacked: bool = False,
    ) -> None:
        """`stacked`: one answer per line."""
        super().__init__()
        self._stacked = stacked
        self._question = question
        self._answers = answers
        self._quote = quote
        self._options = options or TextOptions()

    def compose(self) -> ComposeResult:
        hints = ""
        with Vertical(classes="frame small"), Vertical(classes="dialog"):
            yield BidiLabel(self._question)
            if self._quote:
                yield BidiLabel(
                    self._quote,
                    mode=self._options.mode,
                    lam_alef=self._options.lam_alef,
                    classes="quote",
                )
            if self._stacked:
                for answer in self._answers:
                    yield BidiLabel(f"{answer.key}: {answer.label}", classes="answer")
            else:
                hints = "    ".join(f"{a.key}: {a.label}" for a in self._answers) + "    "
            yield Static(f"{hints}n or Esc: cancel", markup=False, classes="hints")

    def on_key(self, event: events.Key) -> None:
        key = pressed(event)
        answer = next((a for a in self._answers if a.key == key.lower()), None)
        if answer:
            event.stop()
            self.answered(answer.value)
        elif key in ("n", "N", "q", "escape"):
            event.stop()
            self.answered(None)

    def answered(self, value: str | None) -> None:
        self.dismiss(value)


class ConfirmScreen(ChoiceScreen):
    """Dismissed with True or False."""

    def __init__(self, question: str, yes: str) -> None:
        super().__init__(question, [Answer("y", yes, "yes")])

    def answered(self, value: str | None) -> None:
        self.dismiss(value == "yes")  # type: ignore[arg-type]
