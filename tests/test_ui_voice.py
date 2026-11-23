"""Playing and recording voice messages, with a pretend speaker and microphone."""

from dataclasses import replace

import pytest

from driver import OFFLINE, run
from peyk import audio
from peyk.backend import sample
from peyk.backend.fake import FakeBackend
from peyk.config import Config
from peyk.keys import resolve
from peyk.models import State
from peyk.ui.screens.recorder import RecordScreen
from peyk.ui.widgets.status import StatusBar

PHOTOS, SAVED = sample.PHOTOS, 1


def notice(d):
    return d.screen.query_one(StatusBar).notice


def calls(d, name):
    return [argument for found, argument in d.backend.calls if found == name]


def voice_of(d):
    return next(m for m in d.talk.messages if m.media and m.media.kind == "voice")


def label_line(d, message):
    return next(line for line in d.lines() if message.media.label in line)


async def play(d):
    await d.open(PHOTOS)
    message = voice_of(d)
    d.talk.show_message(message.id)
    await d.settle()
    await d.press("o")
    await d.settle(0.3)
    return message


async def tick(d, seconds):
    d.audio.clock.wait(seconds)
    d.screen._voice_tick()
    await d.settle()


# -- playing


async def test_playing_and_where_it_is():
    async with run() as d:
        message = await play(d)
        (playing,) = d.audio.played
        assert playing.rate == audio.RATE and playing.length == pytest.approx(6, abs=0.1)
        assert calls(d, "download") == [f"{PHOTOS}:{message.id}"]
        assert "▶ 0:00 / 0:06" in label_line(d, message)
        await tick(d, 3.2)
        assert "▶ 0:03 / 0:06" in label_line(d, message)
        await tick(d, 3)
        assert "▶" not in label_line(d, message)  # done
        folder = d.screen.folder_for_files
        assert not folder.exists() or not any(folder.iterdir())  # nothing was saved


async def test_playing_again_stops_it():
    async with run() as d:
        message = await play(d)
        await d.press("o")
        assert d.audio.played[0].stopped
        assert "▶" not in label_line(d, message)
        await d.press("o")  # and again plays it, without fetching it again
        await d.settle(0.3)
        assert len(d.audio.played) == 2 and not d.audio.played[1].stopped
        assert len(calls(d, "download")) == 1


async def test_opening_another_chat_stops_it():
    async with run() as d:
        await play(d)
        await d.open(sample.SARA)
        assert d.audio.played[0].stopped
        await d.open(PHOTOS)
        assert "▶" not in label_line(d, voice_of(d))


async def test_stopped_before_it_arrives():
    async with run() as d:
        await d.open(PHOTOS)
        main, message = d.screen, voice_of(d)
        main._play_voice(message)  # the second press comes while it is fetched
        assert "▶ ..." in main.progress.values()
        main._play_voice(message)
        await d.settle(0.5)
        assert d.audio.played == [] and main.progress == {}
        assert "▶" not in label_line(d, message)


async def test_quitting_stops_it():
    async with run() as d:
        await play(d)
        playing = d.audio.played[0]
        assert not playing.stopped
    assert playing.stopped


@pytest.mark.parametrize(("trouble", "said"), [
    ("offline", "Not played. Could not reach Telegram."),
    ("no speaker", audio.NO_SPEAKER),
    ("broken", "Not played. This voice message cannot be played."),
])  # fmt: skip
async def test_when_it_cannot_play(trouble, said):
    async with run() as d:
        if trouble == "offline":
            d.backend.fail("download", OFFLINE)
        d.audio.no_speaker = trouble == "no speaker"
        if trouble == "broken":
            d.backend.sent_files[28] = b"OggS not really"
        message = await play(d)
        assert notice(d).startswith(said)
        assert "▶" not in label_line(d, message)


async def test_other_files_still_open_outside():
    async with run() as d:
        await d.open(PHOTOS)
        video = next(m for m in d.talk.messages if m.media and m.media.kind == "video")
        d.talk.show_message(video.id)
        await d.settle()
        await d.press("o")
        await d.settle(0.5)
        assert d.audio.played == [] and calls(d, "download")


# -- recording


async def record(d, seconds=3.0, chat=SAVED):
    await d.open(chat)
    await d.press("v")
    assert isinstance(d.app.screen, RecordScreen)
    d.audio.clock.wait(seconds)
    d.app.screen.tick()
    await d.settle()


async def test_recording_and_sending():
    async with run() as d:
        await record(d, 3.4)
        text = d.text()
        assert "Voice message to Saved Messages" in text and "● 0:03" in text
        assert "Enter or v: send" in text and "Esc: throw away" in text
        await d.press("enter")
        await d.settle(0.5)
        assert d.audio.recordings[0].finished
        assert calls(d, "upload") == ["voice:3"]
        sent = d.backend.messages[SAVED][-1]
        assert sent.media.kind == "voice" and sent.media.label == "Voice message 0:03"
        assert sent.media.id in d.backend.sent_files  # Ogg/Opus, played back below
        samples, rate = audio.decode(d.backend.sent_files[sent.media.id])
        assert rate == audio.RATE and abs(len(samples) / rate - 3.4) < 0.05
        assert "Voice message 0:03" in d.text()


async def test_the_voice_key_also_sends():
    async with run() as d:
        await record(d)
        await d.press("v")
        await d.settle(0.5)
        assert calls(d, "upload") == ["voice:3"]


async def test_as_a_reply():
    async with run() as d:
        await d.open(sample.GROUP)
        d.talk.show_message(103)
        await d.settle()
        await d.press("r")
        await d.press("escape")  # the reply stays while the box is left
        await record(d, chat=sample.GROUP)
        await d.press("enter")
        await d.settle(0.5)
        assert calls(d, "send")[-1] == f"{sample.GROUP}::103"


async def test_thrown_away():
    async with run() as d:
        await record(d)
        await d.press("escape")
        assert d.audio.recordings[0].cancelled and not isinstance(d.app.screen, RecordScreen)
        assert notice(d) == "The voice message was thrown away."
        assert calls(d, "upload") == []


async def test_other_keys_do_nothing_meanwhile():
    async with run() as d:
        await record(d)
        await d.press("j", "q", "i", "x", "1")
        assert isinstance(d.app.screen, RecordScreen) and not d.audio.recordings[0].cancelled


async def test_too_short_and_silent():
    async with run() as d:
        await record(d, 0.2)
        await d.press("enter")
        assert notice(d) == "That was too short to send."
        d.audio.silent = True
        await record(d)
        await d.press("enter")
        assert "Privacy & security > Microphone" in notice(d)
        assert calls(d, "upload") == []


async def test_it_stops_at_ten_minutes():
    async with run() as d:
        await record(d, audio.LONGEST + 30)
        assert "10:00  the longest a voice message can be" in d.text()
        assert d.audio.recordings[0].finished
        await d.press("enter")
        await d.settle(0.5)
        assert calls(d, "upload") == ["voice:600"]


async def test_no_microphone():
    async with run() as d:
        d.audio.no_microphone = True
        await d.open(SAVED)
        await d.press("v")
        assert notice(d) == audio.NO_MICROPHONE
        assert not isinstance(d.app.screen, RecordScreen)


async def test_where_it_cannot_record():
    backend = FakeBackend()
    news = backend.chat_map[sample.NEWS]
    backend.chat_map[sample.NEWS] = replace(news, can_write=False)
    async with run(backend=backend) as d:
        main = d.screen
        await d.press("v")  # from the list: opens the chat first
        assert main.active == sample.SARA and isinstance(d.app.screen, RecordScreen)
        await d.press("escape")
        await d.open(sample.NEWS)
        await d.press("v")
        assert notice(d) == "You cannot write in this chat."
        assert len(d.audio.recordings) == 1


async def test_not_while_editing():
    async with run() as d:
        await d.open(SAVED)
        await d.press("e")
        await d.press("escape")
        await d.press("v")
        assert notice(d) == "Finish editing first." and d.audio.recordings == []


async def test_recording_stops_what_plays():
    async with run() as d:
        await play(d)
        await d.press("v")
        assert d.audio.played[0].stopped


async def test_quitting_while_recording():
    async with run() as d:
        await record(d)
        recording = d.audio.recordings[0]
    assert recording.cancelled


async def test_unsent_voice_messages_wait_like_others():
    async with run() as d:
        d.backend.fail("upload", OFFLINE)
        await record(d)
        await d.press("enter")
        await d.settle(0.5)
        (waiting,) = d.screen.outbox.waiting(SAVED)
        assert waiting.state is State.WAITING and waiting.media.label == "Voice message 0:03"
        d.talk.show_message(waiting.id)
        await d.settle()
        await d.press("e")
        assert notice(d) == "A voice message cannot be edited. Send it again or discard it."
        d.screen.outbox.retry(SAVED, waiting.id)
        await d.settle(0.5)
        assert d.screen.outbox.waiting(SAVED) == [] and calls(d, "upload") == ["voice:3"] * 2


async def test_keys_help_and_commands():
    keys = resolve({"voice": "ctrl+r"})
    async with run(size=(120, 60), config=Config(keys=keys)) as d:
        await d.open(SAVED)
        await d.press("v")
        assert d.audio.recordings == []
        await d.press("f1")
        text = d.text()
        assert "Ctrl+R" in text and "record a voice message" in text
        assert "play a voice message" in text
        await d.press("f1", ":", *"voice", "enter")
        assert isinstance(d.app.screen, RecordScreen)
        assert "Enter or Ctrl+R: send" in d.text()
