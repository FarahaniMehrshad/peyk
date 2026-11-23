"""Voice messages: playing them, recording them, and Telegram's Ogg/Opus format.

The sound libraries are loaded only when first needed, and the devices are only
touched while something plays or records.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Protocol

import numpy as np

from peyk.models import VoiceNote

log = logging.getLogger(__name__)
RATE = 48_000  # what Telegram's voice messages use
LONGEST = 600  # seconds
SHORTEST = 0.5
BARS = 100  # of the waveform Telegram shows
NO_MICROPHONE = "No microphone was found. Plug one in, or pick one in Windows' sound settings."
BLOCKED = (
    "Only silence came from the microphone. If Windows blocks it: Settings > Privacy & "
    'security > Microphone, turn on "Let desktop apps access your microphone".'
)
NO_SPEAKER = "Sound cannot be played: no speakers or headphones were found."
CANNOT_PLAY = "This voice message cannot be played."


class AudioError(Exception):
    """Said to the user as it is."""


class Playing(Protocol):
    @property
    def position(self) -> float:
        """Seconds played so far."""
        ...

    @property
    def done(self) -> bool: ...

    def stop(self) -> None: ...


class Recording(Protocol):
    @property
    def seconds(self) -> float: ...

    @property
    def level(self) -> float:
        """How loud the last moment was, from 0 to 1."""
        ...

    def finish(self) -> np.ndarray:
        """Stops; mono at RATE, at most LONGEST seconds."""
        ...

    def cancel(self) -> None: ...


class Audio(Protocol):
    def play(self, samples: np.ndarray, rate: int) -> Playing: ...

    def record(self) -> Recording: ...


# -- the format


def decode(data: bytes) -> tuple[np.ndarray, int]:
    """Mono samples and their rate."""
    import soundfile

    try:
        samples, rate = soundfile.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except (RuntimeError, TypeError, ValueError) as error:  # LibsndfileError is a RuntimeError
        raise AudioError(CANNOT_PLAY) from error
    return np.ascontiguousarray(samples.mean(axis=1), dtype=np.float32), int(rate)


def encode(samples: np.ndarray) -> bytes:
    """Ogg/Opus of mono samples at RATE."""
    import soundfile

    out = io.BytesIO()
    soundfile.write(out, np.asarray(samples, dtype=np.float32), RATE, format="OGG",
                    subtype="OPUS")  # fmt: skip
    return out.getvalue()


def waveform(samples: np.ndarray, bars: int = BARS) -> bytes:
    """Levels from 0 to 31, as Telegram draws them."""
    if len(samples) == 0:
        return bytes(bars)
    loud = np.abs(np.asarray(samples, dtype=np.float32))
    peaks = np.array([part.max() if len(part) else 0.0 for part in np.array_split(loud, bars)])
    top = float(peaks.max())
    if top <= 0:
        return bytes(bars)
    return bytes(int(v) for v in np.minimum(31, np.round(peaks / top * 31)))


def silent(samples: np.ndarray) -> bool:
    """Nothing at all: a blocked microphone gives exact zeros."""
    return not np.any(samples)


def voice_note(samples: np.ndarray) -> VoiceNote:
    duration = max(1, round(len(samples) / RATE))
    return VoiceNote(encode(samples), duration, waveform(samples))


def resample(samples: np.ndarray, rate: int, wanted: int) -> np.ndarray:
    if rate == wanted or len(samples) == 0:
        return samples
    count = round(len(samples) * wanted / rate)
    where = np.linspace(0, len(samples) - 1, count)
    return np.interp(where, np.arange(len(samples)), samples).astype(np.float32)


def bar(level: float, width: int = 10) -> str:
    """A level from 0 to 1 as a meter."""
    filled = round(min(1.0, max(0.0, level)) ** 0.5 * width)  # quiet voices still show
    return "█" * filled + "░" * (width - filled)


# -- the devices


def _sounddevice() -> Any:
    try:
        import sounddevice
    except OSError as error:  # PortAudio itself is missing
        raise AudioError(f"Sound is not available here. {error}") from error
    return sounddevice


class SoundDevice:
    """The default speakers and microphone."""

    def play(self, samples: np.ndarray, rate: int) -> Playing:
        return _Playback(_sounddevice(), samples, rate)

    def record(self) -> Recording:
        return _Capture(_sounddevice())


class _Playback:
    def __init__(self, sd: Any, samples: np.ndarray, rate: int) -> None:
        self._sd = sd
        self._at = 0
        self._rate = rate
        self._length = len(samples)
        try:
            device = sd.query_devices(kind="output")
        except (sd.PortAudioError, ValueError) as error:
            raise AudioError(NO_SPEAKER) from error
        error: Exception | None = None
        for wanted in dict.fromkeys((rate, int(device["default_samplerate"]))):
            self._samples = resample(samples, rate, wanted)
            self._scale = wanted / rate
            try:
                self._stream = sd.OutputStream(
                    samplerate=wanted, channels=1, dtype="float32", callback=self._fill
                )
                self._stream.start()
                return
            except sd.PortAudioError as found:
                error = found
        log.info("playback failed: %s", error)
        raise AudioError(NO_SPEAKER) from error

    def _fill(self, out: np.ndarray, frames: int, time: Any, status: Any) -> None:
        chunk = self._samples[self._at : self._at + frames]
        out[: len(chunk), 0] = chunk
        out[len(chunk) :] = 0
        self._at += len(chunk)
        if len(chunk) < frames:
            raise self._sd.CallbackStop

    @property
    def position(self) -> float:
        return min(self._length, self._at / self._scale) / self._rate

    @property
    def done(self) -> bool:
        return not self._stream.active

    def stop(self) -> None:
        try:
            self._stream.abort()
            self._stream.close()
        except self._sd.PortAudioError:
            pass


class _Capture:
    def __init__(self, sd: Any) -> None:
        self._sd = sd
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._level = 0.0
        try:
            device = sd.query_devices(kind="input")
        except (sd.PortAudioError, ValueError) as error:
            raise AudioError(NO_MICROPHONE) from error
        found: list[str] = []
        for rate in dict.fromkeys((RATE, int(device["default_samplerate"]))):
            try:
                self._stream = sd.InputStream(
                    samplerate=rate, channels=1, dtype="float32", callback=self._take
                )
                self._rate = rate
                self._stream.start()
                return
            except sd.PortAudioError as error:
                found.append(str(error))
        log.info("recording failed: %s", "; ".join(found))
        # What Windows says when its privacy settings keep the microphone from apps
        blocked = any("unanticipated host error" in e.lower() for e in found)
        raise AudioError(BLOCKED if blocked else NO_MICROPHONE)

    def _take(self, data: np.ndarray, frames: int, time: Any, status: Any) -> None:
        if self._frames >= LONGEST * self._rate:
            return
        chunk = data[:, 0].copy()
        self._chunks.append(chunk)
        self._frames += len(chunk)
        self._level = float(np.abs(chunk).max()) if len(chunk) else 0.0

    @property
    def seconds(self) -> float:
        return min(LONGEST, self._frames / self._rate)

    @property
    def level(self) -> float:
        return self._level

    def _close(self) -> None:
        try:
            self._stream.abort()
            self._stream.close()
        except self._sd.PortAudioError:
            pass

    def finish(self) -> np.ndarray:
        self._close()
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        samples = np.concatenate(self._chunks)[: LONGEST * self._rate]
        return resample(samples, self._rate, RATE)

    def cancel(self) -> None:
        self._close()
        self._chunks.clear()
