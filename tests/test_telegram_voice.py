"""Sending voice messages through Telethon's own client; only its network call is replaced."""

import numpy as np
import pytest
from telethon import errors, types, utils

from peyk import audio
from peyk.backend import convert
from peyk.backend.base import BackendError, ErrorKind
from test_telegram_files import SavePart, SendMedia, sent_update
from test_telegram_writing import SARA, rig

__all__ = ["rig"]


def note(seconds=2.0):
    wave = np.sin(2 * np.pi * 300 * np.arange(int(seconds * audio.RATE)) / audio.RATE)
    return audio.voice_note((0.3 * wave).astype(np.float32))


async def test_sending_a_voice_message(rig):
    backend, client = rig
    client.replies[SavePart] = True
    client.replies[SendMedia] = sent_update
    voice = note()
    seen = []
    uploaded = await backend.upload(voice, lambda *now: seen.append(now))
    assert seen[-1] == (voice.size, voice.size)
    parts = [r for r in client.requests if isinstance(r, SavePart)]
    assert b"".join(p.bytes for p in parts) == voice.data  # from memory, as it is
    await backend.send(SARA, "", (), 7, 99, uploaded)
    request = client.requests[-1]
    media = request.media
    assert isinstance(media, types.InputMediaUploadedDocument)
    assert media.mime_type == "audio/ogg" and not media.force_file
    (sound,) = media.attributes
    assert isinstance(sound, types.DocumentAttributeAudio)
    assert sound.voice and sound.duration == 2
    assert utils.decode_waveform(sound.waveform)[: len(voice.waveform)] == voice.waveform
    assert (request.random_id, request.reply_to.reply_to_msg_id) == (99, 7)


@pytest.mark.parametrize(("error", "words"), [
    (errors.ChatSendVoicesForbiddenError(None), "This chat does not allow voice messages."),
    (errors.VoiceMessagesForbiddenError(None), "This person does not accept voice messages."),
])  # fmt: skip
async def test_voice_messages_refused(rig, error, words):
    backend, client = rig
    client.replies[SavePart] = True
    client.replies[SendMedia] = error
    uploaded = await backend.upload(note(1), lambda *_: None)
    with pytest.raises(BackendError) as found:
        await backend.send(SARA, "", (), 0, 1, uploaded)
    assert found.value.kind is ErrorKind.REFUSED and found.value.message == words


def test_telethon_still_sees_a_voice_message():
    """So the label and `o` know it for one when it comes back."""
    sound = types.DocumentAttributeAudio(3, voice=True, waveform=b"")
    document = types.Document(5, 1, b"r", None, "audio/ogg", 10, 2, [sound])
    media = types.MessageMediaDocument(document=document, voice=True)
    raw = types.Message(1, types.PeerUser(SARA), None, "", media=media)

    media = convert.message(raw, SARA).media
    assert media.kind == "voice" and media.label == "Voice message 0:03"

