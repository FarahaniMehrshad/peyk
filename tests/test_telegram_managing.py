"""Forwarding, pinning and managing chats through Telethon's own client.

Only its network call is replaced, so the requests it builds are the real ones.
"""

import pytest
from telethon import errors, functions, types

from peyk.backend.base import BackendError, MessagesPinned
from peyk.models import FOREVER
from test_telegram_writing import CHANNEL, DATE, ME, SARA, message, page, rig, updates

__all__ = ["rig"]
m = functions.messages


def asked(client, kind):
    return [r for r in client.requests if isinstance(r, kind)]


def listen(backend):
    heard = []
    backend.on_event(heard.append)
    return heard


# -- forwarding


@pytest.mark.parametrize("with_author", [True, False])
async def test_forwarding(rig, with_author):
    backend, client = rig
    header = types.MessageFwdHeader(DATE, from_id=types.PeerUser(SARA)) if with_author else None
    new = message(9, "hi", types.PeerChannel(30), ME, out=True, fwd_from=header)
    client.replies[m.ForwardMessagesRequest] = updates(
        types.UpdateMessageID(9, 77), types.UpdateNewChannelMessage(new, 1, 1)
    )
    sent = await backend.forward(SARA, 4, CHANNEL, 77, with_author)
    (request,) = asked(client, m.ForwardMessagesRequest)
    assert isinstance(request.from_peer, types.InputPeerUser) and request.from_peer.user_id == SARA
    assert isinstance(request.to_peer, types.InputPeerChannel) and request.to_peer.channel_id == 30
    assert (request.id, request.random_id) == ([4], [77])
    assert request.drop_author is (None if with_author else True)
    assert (sent.id, sent.chat_id, sent.outgoing) == (9, CHANNEL, True)
    assert bool(sent.forwarded) is with_author


async def test_forwarding_again_after_it_went_through(rig):
    backend, client = rig
    client.replies[m.ForwardMessagesRequest] = errors.RandomIdDuplicateError(None)
    assert await backend.forward(SARA, 4, SARA, 77, True) is None


@pytest.mark.parametrize(("error", "words"), [
    (errors.ChatForwardsRestrictedError(None), "does not allow forwarding"),
    (errors.ChatWriteForbiddenError(None), "cannot write"),
])  # fmt: skip
async def test_forwarding_refused(rig, error, words):
    backend, client = rig
    client.replies[m.ForwardMessagesRequest] = error
    with pytest.raises(BackendError, match=words):
        await backend.forward(SARA, 4, SARA, 77, True)


# -- pinned messages


async def test_pinned_messages_newest_first(rig):
    backend, client = rig
    client.replies[m.SearchRequest] = page(message(8, "new"), message(3, "old"))
    found = await backend.pinned(SARA)
    assert [p.id for p in found] == [8, 3]
    (request,) = asked(client, m.SearchRequest)
    assert isinstance(request.filter, types.InputMessagesFilterPinned)


@pytest.mark.parametrize(("arguments", "flags"), [
    ((True,), (None, None, None)),
    ((True, True), (True, None, None)),
    ((True, False, True), (None, None, True)),
    ((False,), (None, True, None)),
])  # fmt: skip
async def test_pinning(rig, arguments, flags):
    backend, client = rig
    client.replies[m.UpdatePinnedMessageRequest] = updates()
    await backend.pin(SARA, 4, *arguments)
    (request,) = asked(client, m.UpdatePinnedMessageRequest)
    assert request.id == 4
    assert (request.silent, request.unpin, request.pm_oneside) == flags


async def test_pinning_refused(rig):
    backend, client = rig
    client.replies[m.UpdatePinnedMessageRequest] = errors.ChatAdminRequiredError(None)
    with pytest.raises(BackendError, match="Only admins"):
        await backend.pin(CHANNEL, 4, True)


async def test_pins_from_elsewhere(rig):
    backend, _ = rig
    heard = listen(backend)
    pinned = types.UpdatePinnedMessages(types.PeerUser(SARA), [4, 5], 1, 1, pinned=True)
    await backend._on_raw(pinned)
    await backend._on_raw(types.UpdatePinnedChannelMessages(30, [6], 1, 1, pinned=None))
    assert heard == [MessagesPinned(SARA, (4, 5), True), MessagesPinned(CHANNEL, (6,), False)]


# -- managing chats


@pytest.mark.parametrize(
    ("until", "sent"), [(FOREVER, 2**31 - 1), (0, 0), (1_800_000_000.7, 1_800_000_000)]
)
async def test_muting(rig, until, sent):
    backend, client = rig
    client.replies[functions.account.UpdateNotifySettingsRequest] = True
    await backend.mute(SARA, until)
    (request,) = asked(client, functions.account.UpdateNotifySettingsRequest)
    assert isinstance(request.peer, types.InputNotifyPeer)
    assert request.settings.mute_until == sent


@pytest.mark.parametrize(("archived", "folder"), [(True, 1), (False, 0)])
async def test_archiving(rig, archived, folder):
    backend, client = rig
    client.replies[functions.folders.EditPeerFoldersRequest] = updates()
    await backend.archive(SARA, archived)
    (request,) = asked(client, functions.folders.EditPeerFoldersRequest)
    assert [p.folder_id for p in request.folder_peers] == [folder]


@pytest.mark.parametrize(("pinned", "flag"), [(True, True), (False, None)])
async def test_pinning_chats(rig, pinned, flag):
    backend, client = rig
    client.replies[m.ToggleDialogPinRequest] = True
    await backend.pin_chat(SARA, pinned)
    (request,) = asked(client, m.ToggleDialogPinRequest)
    assert request.pinned is flag and isinstance(request.peer, types.InputDialogPeer)


async def test_too_many_pinned_chats(rig):
    backend, client = rig
    client.replies[m.ToggleDialogPinRequest] = errors.PinnedDialogsTooMuchError(None)
    with pytest.raises(BackendError, match="Unpin one first"):
        await backend.pin_chat(SARA, True)


@pytest.mark.parametrize(("unread", "flag"), [(True, True), (False, None)])
async def test_marking_unread(rig, unread, flag):
    backend, client = rig
    client.replies[m.MarkDialogUnreadRequest] = True
    await backend.mark_unread(SARA, unread)
    (request,) = asked(client, m.MarkDialogUnreadRequest)
    assert request.unread is flag
