"""Stickers and GIFs through Telethon's own client; only its network call is replaced."""

from datetime import UTC, datetime

import pytest
from telethon import errors, functions, types

from peyk.backend import convert
from peyk.backend.base import BackendError, ErrorKind
from test_telegram_files import borrowed, file_of
from test_telegram_writing import ME, SARA, message, rig, updates

__all__ = ["borrowed", "rig"]

DATE = datetime(2026, 11, 22, tzinfo=UTC)
m = functions.messages
STALE = errors.FileReferenceExpiredError(None)


def sticker(i, emoji="😀", reference=b"ref", thumbs=(), mime="image/webp"):
    attributes = [
        types.DocumentAttributeImageSize(512, 512),
        types.DocumentAttributeSticker(emoji, types.InputStickerSetEmpty()),
    ]
    thumbs = list(thumbs) or [types.PhotoSize("m", 128, 128, 900)]
    return types.Document(id=i, access_hash=i * 10, file_reference=reference, date=DATE,
                          mime_type=mime, size=20_000, dc_id=2, attributes=attributes,
                          thumbs=thumbs)  # fmt: skip


def gif(i, reference=b"ref"):
    attributes = [types.DocumentAttributeVideo(0, 320, 180), types.DocumentAttributeAnimated()]
    return types.Document(id=i, access_hash=i * 10, file_reference=reference, date=DATE,
                          mime_type="video/mp4", size=300_000, dc_id=2, attributes=attributes,
                          thumbs=[types.PhotoSize("m", 320, 180, 900)])  # fmt: skip


def plain_file(i):
    return types.Document(id=i, access_hash=1, file_reference=b"", date=DATE,
                          mime_type="text/plain", size=10, dc_id=2,
                          attributes=[types.DocumentAttributeFilename("a.txt")])  # fmt: skip


def sticker_set(i, title, count, hash_=5, **extra):
    return types.StickerSet(i, i * 10, title, f"set{i}", count, hash_, **extra)


def collection(client, sets=None, recent=None, faved=None):
    client.replies[m.GetRecentStickersRequest] = types.messages.RecentStickers(
        11, [], recent if recent is not None else [sticker(1, "😎"), plain_file(9)], []
    )
    client.replies[m.GetFavedStickersRequest] = types.messages.FavedStickers(
        12, [], faved if faved is not None else [sticker(2, "❤")]
    )
    client.replies[m.GetAllStickersRequest] = types.messages.AllStickers(
        13,
        sets
        or [
            sticker_set(100, "Faces \x1b[31m", 2),
            sticker_set(101, "Old", 4, archived=True),
            sticker_set(102, "Masks", 4, masks=True),
        ],
    )


def asked(client, kind):
    return [r for r in client.requests if isinstance(r, kind)]


# -- what there is


def test_documents_that_are_stickers_or_gifs():
    made = convert.document(sticker(1, "\x1b[31m😎"))
    assert (made.kind, made.id, made.mime, made.picture) == ("sticker", 1, "image/webp", True)
    assert made.label.startswith("Sticker") and "\x1b" not in made.label
    assert (made.width, made.height) == (512, 512)
    moving = convert.document(gif(2))
    assert (moving.kind, moving.label, moving.picture, moving.width) == ("gif", "GIF", True, 320)
    assert convert.document(plain_file(3)) is None
    assert convert.document(types.DocumentEmpty(4)) is None
    animated = sticker(5, thumbs=[types.PhotoPathSize("j", b"")], mime="application/x-tgsticker")
    assert not convert.document(animated).picture  # nothing that can be shown


async def test_packs_are_asked_again_only_when_changed(rig):
    backend, client = rig
    collection(client)
    packs = await backend.sticker_packs()
    assert [(p.key, p.count, [s.id for s in p.stickers]) for p in packs] == [
        ("recent", 1, [1]),
        ("faved", 1, [2]),
        ("set:100", 2, []),
    ]
    assert packs[2].title.startswith("Faces") and "\x1b" not in packs[2].title
    assert [r.hash for r in asked(client, m.GetAllStickersRequest)] == [0]
    client.replies[m.GetRecentStickersRequest] = types.messages.RecentStickersNotModified()
    client.replies[m.GetFavedStickersRequest] = types.messages.FavedStickersNotModified()
    client.replies[m.GetAllStickersRequest] = types.messages.AllStickersNotModified()
    assert await backend.sticker_packs() == packs
    assert [r.hash for r in asked(client, m.GetAllStickersRequest)] == [0, 13]
    assert [r.hash for r in asked(client, m.GetRecentStickersRequest)] == [0, 11]
    assert [r.hash for r in asked(client, m.GetFavedStickersRequest)] == [0, 12]


async def test_empty_lists_are_left_out(rig):
    backend, client = rig
    collection(client, recent=[], faved=[])
    assert [p.key for p in await backend.sticker_packs()] == ["set:100"]


async def test_a_set_is_opened_once_until_it_changes(rig):
    backend, client = rig
    collection(client)
    await backend.sticker_packs()
    client.replies[m.GetStickerSetRequest] = types.messages.StickerSet(
        sticker_set(100, "Faces", 2), [], [], [sticker(3, "😀"), sticker(4, "😂")]
    )
    found = await backend.stickers("set:100")
    assert [s.id for s in found] == [3, 4]
    assert await backend.stickers("set:100") == found
    (request,) = asked(client, m.GetStickerSetRequest)
    assert (request.stickerset.id, request.stickerset.access_hash) == (100, 1000)
    packs = await backend.sticker_packs()
    assert packs[2].stickers == found  # known now
    collection(client, sets=[sticker_set(100, "Faces", 3, hash_=6)])
    await backend.sticker_packs()
    await backend.stickers("set:100")
    assert len(asked(client, m.GetStickerSetRequest)) == 2


async def test_a_set_that_is_gone(rig):
    backend, client = rig
    collection(client)
    await backend.sticker_packs()
    with pytest.raises(BackendError, match="no longer installed"):
        await backend.stickers("set:555")
    client.replies[m.GetStickerSetRequest] = errors.StickersetInvalidError(None)
    with pytest.raises(BackendError, match="no longer exists"):
        await backend.stickers("set:100")


async def test_saved_gifs(rig):
    backend, client = rig
    client.replies[m.GetSavedGifsRequest] = types.messages.SavedGifs(21, [gif(30), gif(31)])
    assert [g.id for g in await backend.saved_gifs()] == [30, 31]
    client.replies[m.GetSavedGifsRequest] = types.messages.SavedGifsNotModified()
    assert [g.id for g in await backend.saved_gifs()] == [30, 31]
    assert [r.hash for r in asked(client, m.GetSavedGifsRequest)] == [0, 21]


# -- sending


async def test_sending_a_sticker_as_a_reply(rig):
    backend, client = rig
    collection(client)
    (recent, *_) = await backend.sticker_packs()
    media = types.MessageMediaDocument(document=sticker(1, "😎"))
    new = message(9, "", out=True, sender=ME, media=media)
    client.replies[m.SendMediaRequest] = updates(
        types.UpdateMessageID(9, 77), types.UpdateNewMessage(new, 1, 1)
    )
    sent = await backend.send(SARA, "", (), 4, 77, recent.stickers[0])
    (request,) = asked(client, m.SendMediaRequest)
    assert isinstance(request.media, types.InputMediaDocument)
    assert (request.media.id.id, request.media.id.access_hash) == (1, 10)
    assert request.media.id.file_reference == b"ref"
    assert (request.message, request.random_id, request.reply_to.reply_to_msg_id) == ("", 77, 4)
    assert (sent.id, sent.media.kind, sent.media.id) == (9, "sticker", 1)


async def test_an_old_reference_is_renewed(rig):
    backend, client = rig
    collection(client)
    (recent, *_) = await backend.sticker_packs()
    collection(client, recent=[sticker(1, "😎", reference=b"new")])
    new = message(9, "", out=True, sender=ME,
                  media=types.MessageMediaDocument(document=sticker(1)))  # fmt: skip

    def send(request):
        if request.media.id.file_reference == b"ref":
            return STALE
        return updates(types.UpdateMessageID(9, 77), types.UpdateNewMessage(new, 1, 1))

    client.replies[m.SendMediaRequest] = send
    assert (await backend.send(SARA, "", (), 0, 77, recent.stickers[0])).id == 9
    assert [r.hash for r in asked(client, m.GetRecentStickersRequest)] == [0, 0]
    assert [r.random_id for r in asked(client, m.SendMediaRequest)] == [77, 77]


async def test_one_that_left_the_collection(rig):
    backend, client = rig
    collection(client)
    (recent, *_) = await backend.sticker_packs()
    collection(client, recent=[sticker(5)])
    client.replies[m.SendMediaRequest] = STALE
    with pytest.raises(BackendError, match="no longer in your collection"):
        await backend.send(SARA, "", (), 0, 77, recent.stickers[0])


@pytest.mark.parametrize(("error", "words"), [
    (errors.ChatSendStickersForbiddenError(None), "does not allow stickers"),
    (errors.ChatSendGifsForbiddenError(None), "does not allow GIFs"),
    (errors.PremiumAccountRequiredError(None), "Premium"),
])  # fmt: skip
async def test_refusals(rig, error, words):
    backend, client = rig
    collection(client)
    (recent, *_) = await backend.sticker_packs()
    client.replies[m.SendMediaRequest] = error
    with pytest.raises(BackendError, match=words) as raised:
        await backend.send(SARA, "", (), 0, 77, recent.stickers[0])
    assert raised.value.kind is ErrorKind.REFUSED


# -- pictures


async def test_pictures_are_the_small_ones(borrowed):
    backend, client = borrowed
    collection(client)
    await backend.sticker_packs()
    wanted = []
    client.replies[functions.upload.GetFileRequest] = file_of(b"w" * 900, wanted)
    assert await backend.file_picture(1, 360) == b"w" * 900
    (location,) = wanted
    assert (location.id, location.thumb_size, location.file_reference) == (1, "m", b"ref")


async def test_pictures_with_an_old_reference(borrowed):
    backend, client = borrowed
    collection(client)
    await backend.sticker_packs()
    collection(client, faved=[sticker(2, reference=b"new")])

    def reply(request):
        if request.location.file_reference == b"ref":
            return STALE
        return file_of(b"w" * 900)(request)

    client.replies[functions.upload.GetFileRequest] = reply
    assert await backend.file_picture(2, 360) == b"w" * 900
    assert [r.hash for r in asked(client, m.GetFavedStickersRequest)] == [0, 0]


async def test_pictures_of_what_is_not_known(rig):
    backend, _ = rig
    with pytest.raises(BackendError, match="no longer in your collection"):
        await backend.file_picture(404, 360)
