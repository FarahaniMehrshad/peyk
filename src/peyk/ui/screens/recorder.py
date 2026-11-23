"""Recording a voice message, until it is sent or thrown away."""

from __future__ import annotations

import numpy as np
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from peyk import audio
from peyk.audio import Recording
from peyk.keys import pretty
from peyk.text import format as fmt
from peyk.ui.keymap import pressed
from peyk.ui.widgets.bidi_label import BidiLabel
from peyk.ui.widgets.strips import TextOptions

TICK = 0.1


class RecordScreen(ModalScreen[np.ndarray | None]):
    """Dismissed with what was recorded, or None if it was thrown away.

    Nothing else can be done meanwhile, so it is always clear that the microphone is on.
    """

    def __init__(
        self, recording: Recording, chat_title: str, voice_key: str, options: TextOptions
    ) -> None:
        super().__init__()
        self._recording = recording
        self._title = chat_title
        self._voice_key = voice_key
        self._options = options
        self._samples: np.ndarray | None = None  # once it stopped by itself
        self._over = False

    def compose(self) -> ComposeResult:
        send = "Enter" + (f" or {pretty(self._voice_key)}" if self._voice_key else "")
        with Vertical(classes="frame small"), Vertical(classes="dialog"):
            yield BidiLabel(
                f"Voice message to {self._title}",
                mode=self._options.mode,
                lam_alef=self._options.lam_alef,
            )
            yield Static("", id="recording")
            yield Static(f"{send}: send    Esc: throw away", markup=False, classes="hints")

    def on_mount(self) -> None:
        self.tick()
        self.set_interval(TICK, self.tick)

    def tick(self) -> None:
        recording = self._recording
        if self._samples is None and recording.seconds >= audio.LONGEST:
            self._samples = recording.finish()
        if self._samples is not None:
            line = Text.assemble(
                ("■ ", "bold"),
                fmt.duration(audio.LONGEST),
                "  the longest a voice message can be",
            )
        else:
            line = Text.assemble(
                ("● ", "bold red"), fmt.duration(recording.seconds), "  ",
                (audio.bar(recording.level), "green"),
            )  # fmt: skip
        self.query_one("#recording", Static).update(line)

    def on_key(self, event: events.Key) -> None:
        event.stop()  # nothing else while the microphone is on
        key = pressed(event)
        if key == "enter" or (self._voice_key and key == self._voice_key):
            samples = self._samples if self._samples is not None else self._recording.finish()
            self._end(samples)
        elif key == "escape":
            self._recording.cancel()
            self._end(None)

    def _end(self, samples: np.ndarray | None) -> None:
        self._over = True
        self.dismiss(samples)

    def on_unmount(self) -> None:
        if not self._over:  # the app is closing
            self._recording.cancel()
