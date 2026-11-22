"""Files through Telethon's own client; only its network call is replaced."""

import asyncio
import io
from datetime import UTC, datetime

import pytest
from PIL import Image
from telethon import errors, functions, types

from peyk.backend import telegram as module
from peyk.backend.base import BackendError, ErrorKind
from peyk.files import Source
from peyk.models import Attachment
from test_telegram_writing import SARA, message, page, rig, updates  # noqa: F401

DATE = datetime(2026, 11, 22, tzinfo=UTC)
GetFile = functions.upload.GetFileRequest
GetMessages = functions.messages.GetMessagesRequest
SavePart = functions.upload.SaveFilePartRequest
SendMedia = functions.messages.SendMediaRequest
STALE = errors.FileReferenceExpiredError(None)


def photo(reference=b"new", sizes=None):
    sizes = sizes or [
        types.PhotoStrippedSize("i", b"\x01\x28\x28" + b"x" * 20),
        types.PhotoSize("m", 320, 240, 300),
        types.PhotoSizeProgressive("y", 1280, 960, [100, 500]),
    ]
    inner = types.Photo(id=77, access_hash=1, file_reference=reference, date=DATE, sizes=sizes,
                        dc_id=2, has_stickers=False)  # fmt: skip
    return types.MessageMediaPhoto(photo=inner)


def document(name="a.txt", mime="text/plain", size=600, thumbs=None, **extra):
    inner = types.Document(
        id=88, access_hash=1, file_reference=b"new", date=DATE, mime_type=mime, size=size, dc_id=2,
        attributes=[types.DocumentAttributeFilename(name), *extra.pop("attributes", [])],
        thumbs=thumbs,
    )  # fmt: skip
    return types.MessageMediaDocument(document=inner, **extra)


def file_of(data, wanted=None):
    def reply(request):
        if wanted is not None:
            wanted.append(request.location)
        return types.upload.File(
            types.storage.FileJpeg(), 0, data[request.offset : request.offset + request.limit]
        )

    return reply


async def load(backend, client, media):
    client.replies[GetMessages] = lambda request: page(message(9, media=media))
    client.replies[functions.messages.GetHistoryRequest] = page(message(9, media=media))
    (found,) = await backend.history(SARA)
    return found


@pytest.fixture
def borrowed(rig, monkeypatch):  # noqa: F811
    """Downloads that come from another data centre use the same replaced call."""
    backend, client = rig

    async def same(sender, request, **_):
        return await client(request)

    async def sender(*_):
        return None

    monkeypatch.setattr(client, "_call", same)
    monkeypatch.setattr(client, "_borrow_exported_sender", sender)
    monkeypatch.setattr(client, "_return_exported_sender", sender)
    client.session.set_dc(2, "127.0.0.1", 443)
    return backend, client


async def test_details_of_a_photo(borrowed):
    found = await load(*borrowed, photo())
    media = found.media
    assert (media.id, media.width, media.height, media.picture) == (77, 1280, 960, True)
    assert media.preview.startswith(b"\xff\xd8")


async def test_the_right_size_is_fetched(borrowed):
    backend, client = borrowed
    await load(backend, client, photo())
    wanted = []
    client.replies[GetFile] = file_of(b"j" * 300, wanted)
    assert await backend.picture(SARA, 9, 300) == b"j" * 300
    assert await backend.picture(SARA, 9, 1000)
    assert await backend.picture(SARA, 9, 5000)
    assert [location.thumb_size for location in wanted] == ["m", "y", "y"]


async def test_thumbnails_of_documents(borrowed):
    backend, client = borrowed
    video = types.DocumentAttributeVideo(12, 1280, 720)
    await load(backend, client, document("a.mp4", "video/mp4", 9_000_000, attributes=[video],
                                         thumbs=[types.PhotoSize("m", 320, 180, 200)]))  # fmt: skip
    wanted = []
    client.replies[GetFile] = file_of(b"t" * 200, wanted)
    assert await backend.picture(SARA, 9, 360) == b"t" * 200
    assert await backend.picture(SARA, 9, 1600) == b"t" * 200  # never the whole video
    assert {location.thumb_size for location in wanted} == {"m"}


async def test_picture_sent_as_a_file(borrowed):
    backend, client = borrowed
    shape = types.DocumentAttributeImageSize(3000, 2000)
    found = await load(backend, client, document("a.png", "image/png", 600, attributes=[shape],
                                         thumbs=[types.PhotoSize("m", 320, 213, 200)]))  # fmt: skip
    assert found.media.picture and (found.media.width, found.media.height) == (3000, 2000)
    wanted = []
    client.replies[GetFile] = file_of(b"t" * 600, wanted)
    await backend.picture(SARA, 9, 300)
    assert len(await backend.picture(SARA, 9, 1600)) == 600
    assert [location.thumb_size for location in wanted] == ["m", ""]


async def test_a_document_without_a_picture(borrowed):
    backend, client = borrowed
    found = await load(backend, client, document())
    assert not found.media.picture and found.media.name == "a.txt"
    assert await backend.picture(SARA, 9, 300) is None


async def test_downloading(borrowed):
    backend, client = borrowed
    await load(backend, client, document(size=300_000))
    client.replies[GetFile] = file_of(b"d" * 300_000)
    sink, seen = io.BytesIO(), []
    await backend.download(SARA, 9, sink, lambda done, total: seen.append((done, total)))
    assert sink.getvalue() == b"d" * 300_000
    assert seen[-1] == (300_000, 300_000)


async def test_an_old_reference_is_renewed_once(borrowed):
    backend, client = borrowed
    await load(backend, client, document())
    good = file_of(b"d" * 600)
    client.replies[GetFile] = [STALE, good]
    sink = io.BytesIO()
    await backend.download(SARA, 9, sink, lambda *_: None)
    assert sink.getvalue() == b"d" * 600

    client.replies[GetFile] = lambda request: STALE
    with pytest.raises(BackendError):
        await backend.download(SARA, 9, io.BytesIO(), lambda *_: None)


async def test_old_reference_of_a_photo(borrowed):
    backend, client = borrowed
    await load(backend, client, photo(reference=b"old"))
    client.replies[GetMessages] = lambda request: page(message(9, media=photo()))

    def reply(request):
        if request.location.file_reference == b"old":
            return STALE
        return file_of(b"j" * 300)(request)

    client.replies[GetFile] = reply
    assert await backend.picture(SARA, 9, 300) == b"j" * 300


async def test_a_message_that_is_gone(borrowed):
    backend, client = borrowed
    client.replies[GetMessages] = lambda request: page()
    with pytest.raises(BackendError) as found:
        await backend.download(SARA, 9, io.BytesIO(), lambda *_: None)
    assert found.value.kind is ErrorKind.REFUSED


async def test_a_full_disk_is_told_apart(borrowed):
    backend, client = borrowed
    await load(backend, client, document())
    client.replies[GetFile] = file_of(b"d" * 600)

    class Full(io.BytesIO):
        def write(self, data):
            raise BackendError(ErrorKind.DISK, "The file cannot be saved: disk full.")

    with pytest.raises(BackendError) as found:
        await backend.download(SARA, 9, Full(), lambda *_: None)
    assert found.value.kind is ErrorKind.DISK


async def test_transfers_that_stall_are_given_up(borrowed, monkeypatch):
    backend, client = borrowed
    monkeypatch.setattr(module, "STALL", 0.05)
    await load(backend, client, document())

    async def never(*_, **__):
        await asyncio.sleep(60)

    monkeypatch.setattr(client, "_call", never)
    with pytest.raises(BackendError) as found:
        await backend.download(SARA, 9, io.BytesIO(), lambda *_: None)
    assert found.value.kind is ErrorKind.NETWORK


async def test_slow_transfers_that_move_are_not(borrowed, monkeypatch):
    backend, _ = borrowed
    monkeypatch.setattr(module, "STALL", 0.1)

    async def slow(tick):
        for done in range(6):
            await asyncio.sleep(0.05)
            tick(done, 6)
        return "done"

    assert await backend._watched(slow, lambda *_: None) == "done"


# -- things that may not be saved


async def test_locked_media(borrowed):
    media = photo()
    media.ttl_seconds = 10
    found = await load(*borrowed, media)
    assert found.media.locked and not found.media.id and not found.media.picture


async def test_protected_chats(borrowed):
    backend, client = borrowed
    raw = message(9, media=photo(), noforwards=True)
    client.replies[functions.messages.GetHistoryRequest] = page(raw)
    (found,) = await backend.history(SARA)
    assert found.media.locked and found.media.picture


# -- sending


def sent_update(request):
    return updates(
        types.UpdateMessageID(id=20, random_id=request.random_id),
        types.UpdateNewMessage(message(20, request.message, out=True), 1, 1),
    )


async def test_sending_a_photo(rig, tmp_path):  # noqa: F811
    backend, client = rig
    path = tmp_path / "a.png"
    Image.new("RGB", (300, 200), "red").save(path)
    client.replies[SavePart] = True
    client.replies[SendMedia] = sent_update
    seen = []
    uploaded = await backend.upload(
        Attachment(path, path.stat().st_size, True), lambda *now: seen.append(now)
    )
    assert seen and seen[-1][0] == seen[-1][1]
    await backend.send(SARA, "caption", (), 3, 555, uploaded)
    request = client.requests[-1]
    assert isinstance(request.media, types.InputMediaUploadedPhoto)
    assert (request.message, request.random_id, request.reply_to.reply_to_msg_id) == (
        "caption", 555, 3,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("name", "mime", "forced"),
    [("a.pdf", "application/pdf", True), ("a.gif", "image/gif", False),
     ("یادداشت.zzz", "application/octet-stream", True)],
)  # fmt: skip
async def test_sending_a_file(rig, tmp_path, name, mime, forced):  # noqa: F811
    backend, client = rig
    path = tmp_path / name
    path.write_bytes(b"x" * 1000)
    client.replies[SavePart] = True
    client.replies[SendMedia] = sent_update
    uploaded = await backend.upload(Attachment(path, 1000), lambda *_: None)
    path.unlink()  # nothing holds it open
    await backend.send(SARA, "", (), 0, 1, uploaded)
    media = client.requests[-1].media
    assert isinstance(media, types.InputMediaUploadedDocument)
    assert media.mime_type == mime and bool(media.force_file) is forced
    assert media.attributes[0].file_name == name


async def test_a_picture_that_is_none_goes_as_a_file(rig, tmp_path):  # noqa: F811
    backend, client = rig
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not a picture")
    client.replies[SavePart] = True
    uploaded = await backend.upload(Attachment(path, 13, True), lambda *_: None)
    assert not uploaded.as_photo


async def test_a_file_that_vanished(rig, tmp_path):  # noqa: F811
    with pytest.raises(BackendError) as found:
        await rig[0].upload(Attachment(tmp_path / "gone.txt", 5), lambda *_: None)
    assert found.value.kind is ErrorKind.DISK


@pytest.mark.parametrize(
    "error",
    [errors.ChatSendPhotosForbiddenError(None), errors.PhotoInvalidDimensionsError(None),
     errors.RPCError(None, "CHAT_SEND_DOCS_FORBIDDEN", 400)],
)  # fmt: skip
async def test_refusals_are_explained(rig, tmp_path, error):  # noqa: F811
    backend, client = rig
    path = tmp_path / "a.txt"
    path.write_bytes(b"x")
    client.replies[SavePart] = True
    client.replies[SendMedia] = error
    uploaded = await backend.upload(Attachment(path, 1), lambda *_: None)
    with pytest.raises(BackendError) as found:
        await backend.send(SARA, "", (), 0, 1, uploaded)
    assert found.value.kind is ErrorKind.REFUSED and "FORBIDDEN" not in found.value.message


def test_sources_have_what_telethon_needs(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"abc")
    source = Source(path)
    assert (source.name, source.size, source.read(2), source.read()) == ("a.txt", 3, b"ab", b"c")
    source.close()
