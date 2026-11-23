"""The part of the main screen that plays and records voice messages."""

from __future__ import annotations

import asyncio
import io
import logging
from functools import partial
from typing import TYPE_CHECKING

import numpy as np
from textual.timer import Timer
from textual.worker import Worker

from peyk import audio
from peyk.audio import AudioError, Playing
from peyk.backend.base import BackendError
from peyk.models import Message
from peyk.text import format as fmt
from peyk.ui.screens.recorder import RecordScreen

if TYPE_CHECKING:
    from peyk.ui.screens.main import MainScreen as _Base
else:
    from textual.screen import Screen as _Base  # a plain mixin would lose its @on handlers

log = logging.getLogger(__name__)
TICK = 0.25
KEPT = 4  # voice messages kept decoded, to play again at once
Key = tuple[int, int]


class VoiceMessages(_Base):
    _voice_key: Key | None = None  # of the message playing, or being fetched to play
    _playing: Playing | None = None
    _voice_length = 0.0
    _voice_work: Worker | None = None
    _voice_timer: Timer | None = None

    # -- playing ------------------------------------------------------------

    def _play_voice(self, message: Message) -> None:
        """Or stop it, if it is the one playing."""
        key = (message.chat_id, message.id)
        if key == self._voice_key:
            self._stop_voice()
            return
        self._stop_voice()
        self._voice_key = key
        self._voice_text("▶ ...")
        # Not a coroutine: if stopped before it starts, there is none left unawaited.
        start = partial(self._start_voice, message)
        self._voice_work = self.run_worker(start, group="voice")

    async def _start_voice(self, message: Message) -> None:
        key = (message.chat_id, message.id)
        try:
            samples, rate = self._decoded.get(key) or await self._fetch_voice(message)
        except (BackendError, AudioError) as error:
            said = error.message if isinstance(error, BackendError) else str(error)
            self._voice_failed(key, f"Not played. {said}")
            return
        self._decoded[key] = (samples, rate)
        self._decoded.move_to_end(key)
        while len(self._decoded) > KEPT:
            self._decoded.popitem(last=False)
        if key != self._voice_key or self._gone:
            return
        try:
            self._playing = self._audio.play(samples, rate)
        except AudioError as error:
            self._voice_failed(key, str(error))
            return
        self._voice_work = None
        self._voice_length = len(samples) / rate
        self._voice_timer = self.set_interval(TICK, self._voice_tick)
        self._voice_tick()

    async def _fetch_voice(self, message: Message) -> tuple[np.ndarray, int]:
        sink = io.BytesIO()
        await self._backend.download(message.chat_id, message.id, sink, lambda *_: None)
        return await asyncio.to_thread(audio.decode, sink.getvalue())

    def _voice_failed(self, key: Key, said: str) -> None:
        if key == self._voice_key:
            self._stop_voice()
        if not self._gone:
            self._flash(said)

    def _voice_tick(self) -> None:
        playing = self._playing
        if playing is None:
            return
        if playing.done:
            self._stop_voice()
            return
        where, length = fmt.duration(playing.position), fmt.duration(self._voice_length)
        self._voice_text(f"▶ {where} / {length}")

    def _voice_text(self, text: str) -> None:
        if self._voice_key is not None:
            self.progress[self._voice_key] = text
            self._progress_changed(self._voice_key[0])

    def _stop_voice(self) -> None:
        if self._voice_work is not None:
            self._voice_work.cancel()
        if self._playing is not None:
            self._playing.stop()
        if self._voice_timer is not None:
            self._voice_timer.stop()
        key = self._voice_key
        self._voice_key = self._playing = self._voice_work = self._voice_timer = None
        if key is not None and self.progress.pop(key, None) is not None and not self._gone:
            self._progress_changed(key[0])

    # -- recording ----------------------------------------------------------

    def do_voice(self) -> None:
        if not self.in_chat and self.chat_list.selected not in (None, self.active):
            self.open_chat(self.chat_list.selected)
        chat = self._chat
        if chat is None:
            self._flash("Open a chat first.")
            return
        if not chat.can_write:
            self._flash("You cannot write in this chat.")
            return
        if self.draft.edit_id:
            self._flash("Finish editing first.")
            return
        self._stop_voice()
        try:
            recording = self._audio.record()
        except AudioError as error:
            self._flash(str(error))
            return
        self.in_chat = True
        self._arrange()

        def recorded(samples: np.ndarray | None) -> None:
            if not self._gone:
                self._recorded(chat.id, samples)

        screen = RecordScreen(recording, chat.title, self._keys["voice"], self._options)
        self.app.push_screen(screen, recorded)

    def _recorded(self, chat_id: int, samples: np.ndarray | None) -> None:
        if samples is None:
            self._flash("The voice message was thrown away.")
        elif len(samples) < audio.SHORTEST * audio.RATE:
            self._flash("That was too short to send.")
        elif audio.silent(samples):
            self._flash(audio.BLOCKED)
        else:
            self.run_worker(self._send_voice(chat_id, samples), group="voice-out")

    async def _send_voice(self, chat_id: int, samples: np.ndarray) -> None:
        try:
            note = await asyncio.to_thread(audio.voice_note, samples)
        except (RuntimeError, OSError, ValueError) as error:
            log.warning("voice message not encoded: %s", error)
            self._flash("The voice message could not be made ready to send.")
            return
        if not self._gone:
            self._send_file(chat_id, note)
