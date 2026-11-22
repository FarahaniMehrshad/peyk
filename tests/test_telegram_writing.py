"""Sending, editing, deleting and searching through Telethon's own client.

Only its network call is replaced, so the requests it builds are the real ones.
"""

from copy import copy
from datetime import UTC, datetime

import pytest
from telethon import TelegramClient, errors, functions, types
from telethon import utils as _utils
from telethon.sessions import MemorySession

from peyk.backend.base import BackendError, ErrorKind
from peyk.backend.telegram import TelegramBackend, translate
from peyk.models import Kind, Span

DATE = datetime(2026, 11, 22, tzinfo=UTC)
ME, SARA = 1, 5
GROUP, CHANNEL = -40, -1_000_000_000_030
ME_USER = types.User(id=ME, first_name="Me", is_self=True, access_hash=1)
SARA_USER = types.User(id=SARA, first_name="Sara", access_hash=1, username="sara")
BASIC = types.Chat(id=40, title="Basic", photo=None, participants_count=3, date=DATE, version=1)
DEVS = types.Channel(id=30, title="Devs", photo=None, date=DATE, megagroup=True, access_hash=1)
PEOPLE = {"users": [ME_USER, SARA_USER], "chats": [BASIC, DEVS]}


class Offline(TelegramClient):
    def __init__(self):
        super().__init__(MemorySession(), 1, "h")
        self.replies = {}
        self.requests = []
        self.learn()

    def learn(self):
        self.session.process_entities(types.contacts.ResolvedPeer(types.PeerUser(ME), **PEOPLE))
        self._mb_entity_cache.set_self_user(ME, False, 1)

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        if isinstance(request, list):
            return [await self(one) for one in request]
        await request.resolve(self, _utils)
        self.requests.append(copy(request))  # Telethon reuses it for the next page
        reply = self.replies[type(request)]
        if isinstance(reply, list):
            reply = reply.pop(0)
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, BaseException):
            raise reply
        return reply



def message(i, text="hi", peer=None, sender=SARA, **extra):
    peer = peer or types.PeerUser(SARA)
    return types.Message(id=i, peer_id=peer, from_id=types.PeerUser(sender), date=DATE,
                         message=text, **extra)  # fmt: skip


def page(*messages, **extra):
    return types.messages.Messages(list(messages), topics=[], **PEOPLE, **extra)


def updates(*found):
    return types.Updates(list(found), PEOPLE["users"], PEOPLE["chats"], DATE, 1)


@pytest.fixture
def rig(home):
    client = Offline()
    backend = TelegramBackend(home.session, 1, "h", client_factory=lambda *a: client)
    backend._client, backend._me = client, ME
    yield backend, client
    client.session.close()


async def test_sending_to_a_person(rig):
    backend, client = rig
    client.replies[functions.messages.SendMessageRequest] = types.UpdateShortSentMessage(
        id=9, pts=1, pts_count=1, date=DATE, out=True,
        entities=[types.MessageEntityBold(3, 4)],
    )  # fmt: skip
    spans = (Span(2, 6, "bold"), Span(7, 8, "link", "https://a.b"), Span(0, 1, "pre", "py"))
    sent = await backend.send(SARA, "👍 bold x", spans, 0, 77)
    request = client.requests[0]
    assert (request.random_id, request.message) == (77, "👍 bold x")
    assert request.reply_to is None
    assert isinstance(request.peer, types.InputPeerUser) and request.peer.user_id == SARA
    made = [(type(e).__name__, e.offset, e.length) for e in request.entities]
    assert made == [("MessageEntityBold", 3, 4), ("MessageEntityTextUrl", 8, 1),
                    ("MessageEntityPre", 0, 2)]  # fmt: skip
    assert not request.clear_draft
    assert (sent.id, sent.chat_id, sent.outgoing, sent.sender_id) == (9, SARA, True, ME)
    assert sent.text[sent.spans[0].start : sent.spans[0].end] == "bold"


@pytest.mark.parametrize(
    ("chat_id", "peer", "update"),
    [
        (GROUP, types.PeerChat(40), types.UpdateNewMessage),
        (CHANNEL, types.PeerChannel(30), types.UpdateNewChannelMessage),
    ],
)
async def test_sending_to_a_group_with_a_reply(rig, chat_id, peer, update):
    backend, client = rig
    header = types.MessageReplyHeader(reply_to_msg_id=4)
    new = message(9, "yes", peer, ME, out=True, reply_to=header)
    client.replies[functions.messages.SendMessageRequest] = updates(
        types.UpdateMessageID(9, 77), update(new, 1, 1)
    )
    original = page(message(4, "shall we?", peer))
    client.replies[functions.messages.GetMessagesRequest] = original
    client.replies[functions.channels.GetMessagesRequest] = original
    sent = await backend.send(chat_id, "yes", (), 4, 77)
    assert client.requests[0].reply_to.reply_to_msg_id == 4
    assert (sent.id, sent.chat_id, sent.outgoing, sent.sender_id) == (9, chat_id, True, ME)
    assert (sent.reply.name, sent.reply.text) == ("Sara", "shall we?")


async def test_sending_again_after_it_had_arrived(rig):
    backend, client = rig
    client.replies[functions.messages.SendMessageRequest] = errors.RandomIdDuplicateError(None)
    assert await backend.send(SARA, "x", (), 0, 77) is None


async def test_refusals_are_explained(rig):
    backend, client = rig
    client.replies[functions.messages.SendMessageRequest] = errors.ChatWriteForbiddenError(None)
    with pytest.raises(BackendError, match="You cannot write in this chat"):
        await backend.send(SARA, "x", (), 0, 77)
    wait = translate(errors.SlowModeWaitError(None, 30))
    assert (wait.kind, wait.retry_after) == (ErrorKind.FLOOD, 30) and "slow mode" in wait.message
    unknown = errors.BadRequestError(None, "REPLY_MESSAGE_ID_INVALID")
    assert "was deleted" in translate(unknown).message


def edited(client, old):
    client.replies[functions.messages.GetMessagesRequest] = page(old)
    client.replies[functions.messages.EditMessageRequest] = lambda request: updates(
        types.UpdateEditMessage(
            message(old.id, request.message, sender=ME, out=True, edit_date=DATE,
                    entities=request.entities), 1, 1,
        )
    )  # fmt: skip


async def test_edit_keeps_formatting_it_does_not_know(rig):
    backend, client = rig
    old = message(9, "👍 quoted and @sara 🎲", sender=ME, out=True, entities=[
        types.MessageEntityBlockquote(3, 6),
        types.MessageEntityMention(14, 5),
        types.MessageEntityCustomEmoji(20, 2, 123),
    ])  # fmt: skip
    edited(client, old)
    result = await backend.edit(SARA, 9, "**ok** 👍 quoted and @sara 🎲")
    request = client.requests[-1]
    assert request.message == "ok 👍 quoted and @sara 🎲"
    made = {type(e).__name__: (e.offset, e.length) for e in request.entities}
    assert made == {"MessageEntityBold": (0, 2), "MessageEntityBlockquote": (6, 6),
                    "MessageEntityCustomEmoji": (23, 2)}  # fmt: skip
    assert next(e for e in request.entities if hasattr(e, "document_id")).document_id == 123
    assert old.entities[0].offset == 3  # the original is not touched
    assert result.text == request.message and result.edited and result.outgoing


async def test_edit_leaves_the_link_preview_as_it_was(rig):
    backend, client = rig
    old = message(9, "see https://a.b", sender=ME, out=True,
                  entities=[types.MessageEntityUrl(4, 11)])  # fmt: skip
    edited(client, old)
    await backend.edit(SARA, 9, "see https://a.b now")
    assert client.requests[-1].no_webpage is True
    edited(client, message(9, "plain", sender=ME, out=True))
    await backend.edit(SARA, 9, "plainer")
    assert client.requests[-1].no_webpage is None


async def test_edit_without_changes_or_of_a_missing_message(rig):
    backend, client = rig
    edited(client, message(9, "same", sender=ME, out=True))
    client.replies[functions.messages.EditMessageRequest] = errors.MessageNotModifiedError(None)
    assert (await backend.edit(SARA, 9, "same")).text == "same"
    client.replies[functions.messages.GetMessagesRequest] = page(types.MessageEmpty(9, None))
    with pytest.raises(BackendError, match="no longer exists"):
        await backend.edit(SARA, 9, "x")


async def test_edit_too_late(rig):
    backend, client = rig
    edited(client, message(9, "old", sender=ME, out=True))
    client.replies[functions.messages.EditMessageRequest] = errors.MessageEditTimeExpiredError(None)
    with pytest.raises(BackendError, match="too old"):
        await backend.edit(SARA, 9, "new")


async def test_deleting(rig):
    backend, client = rig
    done = types.messages.AffectedMessages(1, 1)
    client.replies[functions.messages.DeleteMessagesRequest] = done
    client.replies[functions.channels.DeleteMessagesRequest] = done
    await backend.delete(SARA, [9], for_everyone=False)
    await backend.delete(GROUP, [9], for_everyone=True)
    await backend.delete(CHANNEL, [9], for_everyone=True)
    private, group, channel = client.requests
    assert not private.revoke and group.revoke and private.id == [9]
    assert isinstance(channel, functions.channels.DeleteMessagesRequest)


# -- reading around, searching


def slice_of(*messages):
    return types.messages.MessagesSlice(count=500, messages=list(messages), topics=[], **PEOPLE)


async def test_messages_after_one(rig):
    backend, client = rig
    client.replies[functions.messages.GetHistoryRequest] = [
        slice_of(message(13), message(12), message(11))
    ]
    found = await backend.history(SARA, after=10, limit=3)
    assert [m.id for m in found] == [11, 12, 13]
    request = client.requests[0]
    assert (request.offset_id, request.add_offset, request.limit) == (11, -3, 3)


async def test_messages_around_one(rig):
    backend, client = rig
    client.replies[functions.messages.GetHistoryRequest] = [
        slice_of(*(message(i) for i in (12, 11, 10, 9)))
    ]
    found = await backend.around(SARA, 11, limit=4)
    assert [m.id for m in found] == [9, 10, 11, 12]
    request = client.requests[0]
    assert (request.offset_id, request.add_offset, request.limit) == (11, -2, 4)


async def test_search_in_a_chat(rig, monkeypatch):
    monkeypatch.setattr("peyk.backend.telegram.SEARCH_BATCH", 2)
    backend, client = rig
    client.replies[functions.messages.SearchRequest] = page(*(message(i) for i in (9, 8, 7)))
    found = [batch async for batch in backend.search("hi", SARA)]
    assert [[f.message.id for f in batch] for batch in found] == [[9, 8], [7]]
    assert found[0][0].chat is None and client.requests[0].q == "hi"


async def test_search_by_sender_in_a_group(rig):
    backend, client = rig
    client.replies[functions.messages.SearchRequest] = page(message(9, peer=types.PeerChannel(30)))
    found = [f async for batch in backend.search("", CHANNEL, SARA) for f in batch]
    assert [f.message.sender for f in found] == ["Sara"]
    assert client.requests[0].from_id.user_id == SARA


async def test_search_everywhere(rig):
    backend, client = rig
    client.replies[functions.messages.SearchGlobalRequest] = page(
        message(9), message(3, peer=types.PeerChannel(30)), message(4, peer=types.PeerChat(40))
    )
    found = [f async for batch in backend.search("hi") for f in batch]
    assert [f.chat.title for f in found] == ["Sara", "Devs", "Basic"]
    assert [f.message.chat_id for f in found] == [SARA, CHANNEL, GROUP]


async def test_find_chats(rig):
    backend, client = rig
    client.replies[functions.contacts.SearchRequest] = types.contacts.Found(
        my_results=[types.PeerUser(SARA), types.PeerChannel(30)],
        results=[types.PeerUser(99)], **PEOPLE,
    )  # fmt: skip
    found = await backend.find_chats("sa")
    assert [(c.title, c.kind, c.username) for c in found] == [
        ("Sara", Kind.USER, "sara"), ("Devs", Kind.GROUP, ""),
    ]  # fmt: skip


async def test_members(rig):
    backend, client = rig
    client.replies[functions.channels.GetParticipantsRequest] = (
        types.channels.ChannelParticipants(
            count=1, participants=[types.ChannelParticipant(SARA, DATE)], **PEOPLE
        )
    )
    assert [(u.name, u.username) for u in await backend.members(CHANNEL, "sa")] == [
        ("Sara", "sara")
    ]
    refusal = errors.ChatAdminRequiredError(None)
    client.replies[functions.channels.GetParticipantsRequest] = refusal
    assert await backend.members(CHANNEL, "sa") == []
