"""Speakers and a microphone that are not there: nothing touches a real device.

The microphone hears a steady tone. Time is a clock the test moves forward.
"""

from __future__ import annotations

import math
import time

import numpy as np

from peyk import audio
from peyk.audio import AudioError


class Clock:
    def __init__(self, real: bool = False) -> None:
        self._real = real
        self.now = 0.0

    def __call__(self) -> float:
        return time.monotonic() if self._real else self.now

    def wait(self, seconds: float) -> None:
        self.now += seconds


class FakePlaying:
    def __init__(self, clock: Clock, samples: np.ndarray, rate: int) -> None:
        self._clock = clock
        self._start = clock()
        self.samples, self.rate = samples, rate
        self.length = len(samples) / rate
        self.stopped = False

    @property
    def position(self) -> float:
        return min(self.length, self._clock() - self._start)

    @property
    def done(self) -> bool:
        return self.stopped or self.position >= self.length

    def stop(self) -> None:
        self.stopped = True


class FakeRecording:
    def __init__(self, clock: Clock, silent: bool) -> None:
        self._clock = clock
        self._start = clock()
        self._silent = silent
        self.finished = self.cancelled = False

    @property
    def seconds(self) -> float:
        return min(audio.LONGEST, self._clock() - self._start)

    @property
    def level(self) -> float:
        return 0.0 if self._silent else 0.4 + 0.3 * math.sin(self.seconds * 5)

    def finish(self) -> np.ndarray:
        self.finished = True
        count = int(self.seconds * audio.RATE)
        if self._silent:
            return np.zeros(count, dtype=np.float32)
        wave = np.sin(2 * np.pi * 440 * np.arange(count) / audio.RATE)
        return (0.3 * wave).astype(np.float32)

    def cancel(self) -> None:
        self.cancelled = True


class FakeAudio:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or Clock()
        self.played: list[FakePlaying] = []
        self.recordings: list[FakeRecording] = []
        self.no_speaker = self.no_microphone = self.silent = False

    def play(self, samples: np.ndarray, rate: int) -> FakePlaying:
        if self.no_speaker:
            raise AudioError(audio.NO_SPEAKER)
        playing = FakePlaying(self.clock, samples, rate)
        self.played.append(playing)
        return playing

    def record(self) -> FakeRecording:
        if self.no_microphone:
            raise AudioError(audio.NO_MICROPHONE)
        recording = FakeRecording(self.clock, self.silent)
        self.recordings.append(recording)
        return recording

    @property
    def playing(self) -> FakePlaying | None:
        return next((p for p in reversed(self.played) if not p.done), None)
