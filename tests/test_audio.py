"""Voice messages: the format, and the device code against a stand-in for sounddevice."""

import numpy as np
import pytest

from peyk import audio
from peyk.audio import AudioError

RATE = audio.RATE


def tone(seconds, loud=0.3, pitch=440):
    time = np.arange(int(seconds * RATE)) / RATE
    return (loud * np.sin(2 * np.pi * pitch * time)).astype(np.float32)


# -- the format


def test_ogg_opus_both_ways():
    data = audio.encode(tone(2))
    assert data[:4] == b"OggS" and b"OpusHead" in data[:100]
    samples, rate = audio.decode(data)
    assert rate == RATE and abs(len(samples) - 2 * RATE) < RATE // 50
    assert samples.dtype == np.float32 and 0.2 < np.abs(samples).max() < 0.4


def test_stereo_becomes_mono():
    import io

    import soundfile

    out = io.BytesIO()
    soundfile.write(out, np.stack([tone(1), tone(1)], axis=1), RATE, format="OGG",
                    subtype="OPUS")  # fmt: skip
    samples, _ = audio.decode(out.getvalue())
    assert samples.ndim == 1


@pytest.mark.parametrize("data", [b"", b"not a sound at all", b"OggS" + b"\0" * 100])
def test_what_cannot_be_played(data):
    with pytest.raises(AudioError, match="cannot be played"):
        audio.decode(data)


def test_waveform():
    rising = np.linspace(0, 1, 100 * 480, dtype=np.float32)
    levels = audio.waveform(rising)
    assert len(levels) == 100 and levels[-1] == 31 and levels[0] == 0
    assert list(levels) == sorted(levels)
    assert audio.waveform(np.zeros(5000, np.float32)) == bytes(100)
    assert audio.waveform(np.zeros(0, np.float32)) == bytes(100)
    assert len(audio.waveform(tone(0.0001))) == 100  # fewer samples than bars


def test_a_voice_note():
    note = audio.voice_note(tone(3.4))
    assert note.duration == 3 and note.name == "voice.ogg" and note.size == len(note.data)
    assert set(note.waveform) <= set(range(32)) and max(note.waveform) == 31
    assert audio.voice_note(tone(0.2)).duration == 1


def test_silence_and_meter():
    assert audio.silent(np.zeros(100, np.float32))
    assert not audio.silent(tone(0.01, loud=0.001))
    assert audio.bar(0) == "░" * 10 and audio.bar(1) == "█" * 10 and audio.bar(7) == "█" * 10
    assert audio.bar(0.04).count("█") == 2  # quiet voices still move it


def test_resampling():
    samples = tone(1)
    assert audio.resample(samples, RATE, RATE) is samples
    half = audio.resample(samples, RATE, 24_000)
    assert len(half) == 24_000 and half.dtype == np.float32


# -- the devices


class Stream:
    def __init__(self, sd, rate, callback):
        self.sd, self.rate, self.callback = sd, rate, callback
        self.active = self.started = self.closed = False

    def start(self):
        if self.sd.fail_start:
            raise self.sd.PortAudioError(self.sd.fail_start)
        self.active = self.started = True

    def abort(self):
        self.active = False

    def close(self):
        self.closed = True


class FakeSd:
    class PortAudioError(Exception):
        pass

    class CallbackStop(Exception):  # noqa: N818
        pass

    def __init__(self, rates=(RATE,), default=44_100, present=True, fail_start=""):
        self.rates, self.default, self.present = rates, default, present
        self.fail_start = fail_start
        self.streams = []

    def query_devices(self, kind):
        if not self.present:
            raise ValueError(f"No {kind} device")
        return {"default_samplerate": float(self.default)}

    def _open(self, samplerate, channels, dtype, callback):
        assert (channels, dtype) == (1, "float32")
        if samplerate not in self.rates:
            raise self.PortAudioError("Invalid sample rate")
        stream = Stream(self, samplerate, callback)
        self.streams.append(stream)
        return stream

    OutputStream = InputStream = _open


@pytest.fixture
def sd(monkeypatch):
    found = FakeSd()
    monkeypatch.setattr(audio, "_sounddevice", lambda: found)
    return found


def play_out(playing, stream, frames=1000):
    """Asks for sound the way PortAudio does, until the end."""
    heard = []
    while True:
        out = np.full((frames, 1), 9.0, dtype=np.float32)
        try:
            stream.callback(out, frames, None, None)
        except FakeSd.CallbackStop:
            heard.append(out[:, 0])
            return np.concatenate(heard)
        heard.append(out[:, 0])


def test_playing(sd):
    samples = tone(0.05)
    playing = audio.SoundDevice().play(samples, RATE)
    (stream,) = sd.streams
    assert stream.started and not playing.done and playing.position == 0
    heard = play_out(playing, stream)
    assert np.array_equal(heard[: len(samples)], samples)
    assert not heard[len(samples) :].any()  # silence after it, not leftovers
    assert playing.position == pytest.approx(0.05)
    playing.stop()
    assert playing.done and stream.closed


def test_playing_where_the_rate_is_not_supported(sd):
    sd.rates, sd.default = (44_100,), 44_100
    playing = audio.SoundDevice().play(tone(1), RATE)
    (stream,) = sd.streams
    assert stream.rate == 44_100
    heard = play_out(playing, stream, 4410)
    assert abs(len(np.trim_zeros(heard, "b")) - 44_100) < 50
    assert playing.position == pytest.approx(1, abs=0.01)


@pytest.mark.parametrize("trouble", ["absent", "refused"])
def test_no_speaker(sd, trouble):
    sd.present = trouble != "absent"
    sd.fail_start = "Device unavailable" if trouble == "refused" else ""
    with pytest.raises(AudioError, match="no speakers"):
        audio.SoundDevice().play(tone(1), RATE)


def test_recording(sd):
    recording = audio.SoundDevice().record()
    (stream,) = sd.streams
    assert stream.rate == RATE and stream.started
    for part in np.array_split(tone(0.5), 10):
        stream.callback(part.reshape(-1, 1), len(part), None, None)
    assert recording.seconds == pytest.approx(0.5)
    assert recording.level == pytest.approx(0.3, abs=0.01)
    samples = recording.finish()
    assert stream.closed and len(samples) == RATE // 2
    assert np.allclose(samples, tone(0.5))


def test_recording_at_the_device_rate(sd):
    sd.rates, sd.default = (16_000,), 16_000
    recording = audio.SoundDevice().record()
    data = np.zeros((16_000, 1), np.float32)
    sd.streams[0].callback(data, 16_000, None, None)
    assert recording.seconds == pytest.approx(1)
    assert len(recording.finish()) == RATE


def test_recording_stops_at_the_longest(sd, monkeypatch):
    monkeypatch.setattr(audio, "LONGEST", 1)
    recording = audio.SoundDevice().record()
    second = np.ones((RATE, 1), np.float32) * 0.1
    for _ in range(3):
        sd.streams[0].callback(second, RATE, None, None)
    assert recording.seconds == 1 and len(recording.finish()) == RATE


def test_recording_thrown_away(sd):
    recording = audio.SoundDevice().record()
    sd.streams[0].callback(np.ones((480, 1), np.float32), 480, None, None)
    recording.cancel()
    assert sd.streams[0].closed and len(recording.finish()) == 0


@pytest.mark.parametrize(("trouble", "said"), [
    ("absent", "No microphone"),
    ("Invalid device", "No microphone"),
    ("Unanticipated host error", "Privacy & security"),
])  # fmt: skip
def test_no_microphone(sd, trouble, said):
    sd.present = trouble != "absent"
    sd.fail_start = "" if trouble == "absent" else trouble
    with pytest.raises(AudioError, match=said):
        audio.SoundDevice().record()


def test_sounddevice_itself_missing(monkeypatch):
    import builtins

    real = builtins.__import__

    def without(name, *rest):
        if name == "sounddevice":
            raise OSError("PortAudio library not found")
        return real(name, *rest)

    monkeypatch.undo()  # the guard in conftest replaces the loader
    monkeypatch.setattr(builtins, "__import__", without)
    with pytest.raises(AudioError, match="PortAudio library not found"):
        audio._sounddevice()
