"""Reactions and typing through Telethon's own client; only its network call is replaced."""

import asyncio
from types import SimpleNamespace

import pytest
from telethon import errors, functions, types

from peyk.backend import convert
from peyk.backend.base import (
    BackendError,
    ErrorKind,
    MessageChanged,
    ReactionsChanged,
    Typing,
)
from peyk.backend.telegram import TelegramBackend
from peyk.models import Reaction
from test_telegram_writing import CHANNEL, GROUP, ME, SARA, SARA_USER, message, page, rig, updates

__all__ = ["rig"]
THUMB, HEART = types.ReactionEmoji("👍"), types.ReactionEmoji("❤")
FOX, OLD = types.ReactionCustomEmoji(77), types.ReactionEmoji("🗿")


def given(*counts, **extra):
    results = [types.ReactionCount(r, n, chosen_order=o) for r, n, o in counts]
    return types.MessageReactions(results, **extra)


def available(*items):
    icon = types.DocumentEmpty(1)
    return types.messages.AvailableReactions(1, [
        types.AvailableReaction(emoji, name, icon, icon, icon, icon, icon,
                                premium=premium, inactive=inactive)
        for emoji, name, premium, inactive in items
    ])  # fmt: skip


def fox_document():
    alt = types.DocumentAttributeCustomEmoji("🦊", types.InputStickerSetEmpty())
    return types.Document(77, 1, b"", None, "image/webp", 1, 2, [alt])


ALL = available(("👍", "Thumbs Up", None, None), ("❤", "Red Heart", None, None),
                ("🐳", "Whale", True, None), ("🗿", "Old", None, True))  # fmt: skip


def listen(backend):
    heard = []
    backend.on_event(heard.append)
    return heard


# -- reading


def test_reactions_of_a_message():
    raw = message(1, reactions=given((THUMB, 3, None), (HEART, 1, 0), (FOX, 2, None),
                                     (types.ReactionPaid(), 5, None)))  # fmt: skip
    made = convert.message(raw, SARA, names={77: "🦊"})
    assert made.reactions == (
        Reaction("👍", "👍", 3),
        Reaction("❤", "❤️", 1, mine=True),
        Reaction("custom:77", "🦊", 2),
        Reaction("paid", "⭐", 5),
    )
    assert not made.reactions_shared
    assert convert.message(raw, SARA).reactions[2].label == convert.UNKNOWN_EMOJI
    shared = message(1, reactions=given((THUMB, 3, None), min=True))
    assert convert.message(shared, SARA).reactions_shared
    assert convert.message(message(1), SARA).reactions == ()


def test_odd_reactions():
    raw = given((types.ReactionEmpty(), 1, None), (THUMB, 0, None),
                (types.ReactionEmoji("\x1b[31mred\x07"), 1, None))  # fmt: skip
    (only,) = convert.reactions(raw, {})
    assert "\x1b" not in only.label and len(only.label) <= 17


def test_keys_go_back_to_what_they_were():
    for raw in (THUMB, HEART, FOX):
        assert convert.raw_reaction(convert.reaction_key(raw)) == raw


def test_silent_messages():
    assert convert.message(message(1, silent=True), SARA).silent
    assert not convert.message(message(1), SARA).silent


async def test_custom_emoji_are_learned_once(rig):
    backend, client = rig
    raw = message(1, reactions=given((FOX, 2, None)))
    client.replies[functions.messages.GetHistoryRequest] = lambda r: page(raw)
    client.replies[functions.messages.GetCustomEmojiDocumentsRequest] = [[fox_document()]]
    for _ in range(2):
        (made,) = await backend.history(SARA)
        assert made.reactions[0].label == "🦊"
    asked = [r for r in client.requests
             if isinstance(r, functions.messages.GetCustomEmojiDocumentsRequest)]  # fmt: skip
    assert [r.document_id for r in asked] == [[77]]


async def test_history_does_not_need_the_custom_emoji(rig):
    backend, client = rig
    raw = message(1, reactions=given((FOX, 2, None)))
    client.replies[functions.messages.GetHistoryRequest] = lambda r: page(raw)
    client.replies[functions.messages.GetCustomEmojiDocumentsRequest] = [
        ConnectionError(), [],  # then it does not exist
    ]  # fmt: skip
    for _ in range(3):
        (made,) = await backend.history(SARA)
        assert made.reactions[0].label == convert.UNKNOWN_EMOJI
    assert client.replies[functions.messages.GetCustomEmojiDocumentsRequest] == []


# -- the menu


async def test_menu_of_a_private_chat(rig):
    backend, client = rig
    client.replies[functions.messages.GetAvailableReactionsRequest] = ALL
    top = types.messages.Reactions(1, [HEART, FOX])
    client.replies[functions.messages.GetTopReactionsRequest] = top
    menu = await backend.reaction_menu(SARA)
    assert [(c.key, c.label, c.name) for c in menu.choices] == [
        ("❤", "❤️", "Red Heart"), ("👍", "👍", "Thumbs Up"),
    ]  # fmt: skip
    assert menu.most == 1
    count = len(client.requests)
    assert await backend.reaction_menu(SARA) is menu
    assert len(client.requests) == count  # remembered


async def test_premium_has_more(rig):
    backend, client = rig
    backend._premium = True
    client.replies[functions.messages.GetAvailableReactionsRequest] = ALL
    client.replies[functions.messages.GetTopReactionsRequest] = ConnectionError()
    menu = await backend.reaction_menu(SARA)
    assert [c.key for c in menu.choices] == ["👍", "❤", "🐳"] and menu.most == 3


def full(allowed):
    chat = SimpleNamespace(available_reactions=allowed)
    return SimpleNamespace(full_chat=chat)


@pytest.mark.parametrize(("chat_id", "request_type"), [
    (GROUP, functions.messages.GetFullChatRequest),
    (CHANNEL, functions.channels.GetFullChannelRequest),
])  # fmt: skip
async def test_groups_decide_what_is_allowed(rig, chat_id, request_type):
    backend, client = rig
    client.replies[functions.messages.GetAvailableReactionsRequest] = ALL
    client.replies[functions.messages.GetTopReactionsRequest] = types.messages.Reactions(1, [])
    client.replies[functions.messages.GetCustomEmojiDocumentsRequest] = lambda r: [fox_document()]
    for allowed, keys in (
        (types.ChatReactionsAll(), ["👍", "❤"]),
        (types.ChatReactionsNone(), []),
        (None, []),
        (types.ChatReactionsSome([HEART, FOX, OLD]), ["❤", "custom:77", "🗿"]),
    ):
        backend._menus.clear()
        client.replies[request_type] = full(allowed)
        menu = await backend.reaction_menu(chat_id)
        assert [c.key for c in menu.choices] == keys
    assert [c.label for c in menu.choices] == ["❤️", "🦊", "🗿"]
    assert menu.choices[0].name == "Red Heart"


# -- reacting


async def test_reacting(rig):
    backend, client = rig
    update = types.UpdateMessageReactions(types.PeerUser(SARA), 9, given((THUMB, 2, 0)))
    client.replies[functions.messages.SendReactionRequest] = updates(update)
    found = await backend.react(SARA, 9, ("👍", "custom:77"))
    request = client.requests[-1]
    assert (request.msg_id, request.reaction, request.add_to_recent) == (9, [THUMB, FOX], True)
    assert found == (Reaction("👍", "👍", 2, True),)


async def test_taking_back(rig):
    backend, client = rig
    update = types.UpdateMessageReactions(types.PeerUser(SARA), 9, given())
    client.replies[functions.messages.SendReactionRequest] = updates(update)
    assert await backend.react(SARA, 9, ()) == ()
    request = client.requests[-1]
    assert request.reaction == [] and not request.add_to_recent
    assert request._bytes()  # an empty list can be sent


async def test_the_message_is_fetched_if_the_answer_does_not_tell(rig):
    backend, client = rig
    everyones = given((THUMB, 2, None), min=True)
    shared = types.UpdateMessageReactions(types.PeerUser(SARA), 9, everyones)
    other = types.UpdateMessageReactions(types.PeerUser(SARA), 8, given((HEART, 1, 0)))
    client.replies[functions.messages.SendReactionRequest] = updates(other, shared)
    raw = message(9, reactions=given((THUMB, 2, 0)))
    client.replies[functions.messages.GetMessagesRequest] = lambda r: page(raw)
    assert await backend.react(SARA, 9, ("👍",)) == (Reaction("👍", "👍", 2, True),)


async def test_reacting_again_is_not_an_error(rig):
    backend, client = rig
    client.replies[functions.messages.SendReactionRequest] = errors.MessageNotModifiedError(None)
    raw = message(9, reactions=given((THUMB, 1, 0)))
    client.replies[functions.messages.GetMessagesRequest] = lambda r: page(raw)
    assert await backend.react(SARA, 9, ("👍",)) == (Reaction("👍", "👍", 1, True),)


@pytest.mark.parametrize(("error", "words"), [
    (errors.ReactionInvalidError(None), "not allowed here"),
    (errors.ReactionsTooManyError(None), "too many different"),
    (errors.PremiumAccountRequiredError(None), "needs Telegram Premium"),
    (errors.RPCError(None, "ANONYMOUS_REACTIONS_DISABLED", 400), "Anonymous admins"),
    (errors.MessageIdInvalidError(None), "no longer exists"),
])  # fmt: skip
async def test_refusals(rig, error, words):
    backend, client = rig
    client.replies[functions.messages.SendReactionRequest] = error
    with pytest.raises(BackendError) as caught:
        await backend.react(SARA, 9, ("👍",))
    assert caught.value.kind is ErrorKind.REFUSED and words in caught.value.message


# -- live updates


async def test_reactions_arrive(rig):
    backend, client = rig
    heard = listen(backend)
    for peer in (types.PeerUser(SARA), types.PeerChat(40), types.PeerChannel(30)):
        await backend._on_raw(types.UpdateMessageReactions(peer, 9, given((THUMB, 1, None))))
    await backend._on_raw(
        types.UpdateMessageReactions(types.PeerUser(SARA), 9, given((HEART, 1, None), min=True))
    )
    assert [(e.chat_id, e.message_id, e.reactions[0].key, e.shared) for e in heard] == [
        (SARA, 9, "👍", False), (GROUP, 9, "👍", False), (CHANNEL, 9, "👍", False),
        (SARA, 9, "❤", True),
    ]  # fmt: skip


async def test_reactions_that_come_as_an_edit(rig):
    backend, client = rig
    heard = listen(backend)
    raw = message(9, reactions=given((FOX, 1, None)), edit_hide=True)
    client.replies[functions.messages.GetCustomEmojiDocumentsRequest] = lambda r: [fox_document()]

    async def get_sender():
        return SARA_USER

    raw._finish_init(client, {SARA: SARA_USER}, None)
    await backend._on_edited(SimpleNamespace(chat_id=SARA, message=raw, get_sender=get_sender))
    (event,) = heard
    assert isinstance(event, MessageChanged) and not event.message.edited
    assert event.message.reactions == (Reaction("custom:77", "🦊", 1),)


async def test_asking_for_reactions(rig):
    backend, client = rig
    heard = listen(backend)
    client.replies[functions.messages.GetMessagesReactionsRequest] = updates(
        types.UpdateMessageReactions(types.PeerChannel(30), 9, given((THUMB, 1, 0)))
    )
    await backend.refresh_reactions(CHANNEL, [8, 9])
    assert client.requests[-1].id == [8, 9]
    assert heard == [ReactionsChanged(CHANNEL, 9, (Reaction("👍", "👍", 1, True),))]


async def test_telling_that_we_type(rig):
    backend, client = rig
    client.replies[functions.messages.SetTypingRequest] = True
    await backend.typing(SARA)
    request = client.requests[-1]
    assert isinstance(request.action, types.SendMessageTypingAction)
    assert request.peer.user_id == SARA
    client.replies[functions.messages.SetTypingRequest] = errors.ChatWriteForbiddenError(None)
    with pytest.raises(BackendError):
        await backend.typing(SARA)


async def test_others_type(rig):
    backend, client = rig
    heard = listen(backend)
    typing, voice = types.SendMessageTypingAction(), types.SendMessageRecordAudioAction()
    group = types.UpdateChatUserTyping(40, types.PeerUser(SARA), voice)
    group._entities = {SARA: SARA_USER}
    for update in (
        types.UpdateUserTyping(SARA, typing),
        group,
        types.UpdateChannelUserTyping(30, types.PeerUser(SARA), typing, top_msg_id=3),
        types.UpdateUserTyping(SARA, types.SendMessageCancelAction()),
        types.UpdateUserTyping(SARA, types.SendMessageEmojiInteractionSeen("👍")),
        types.UpdateChatUserTyping(40, types.PeerUser(ME), typing),  # ourselves, elsewhere
    ):
        await backend._on_raw(update)
    assert heard == [
        Typing(SARA, SARA, "typing"),
        Typing(GROUP, SARA, "recording a voice message", "Sara"),
        Typing(CHANNEL, SARA, "typing"),
        Typing(SARA, SARA, ""),
    ]


def test_every_kind_of_doing_is_known_or_ignored():
    known = set(convert._DOING) | set(convert._STOPPED)
    ignored = {
        types.SendMessageEmojiInteraction, types.SendMessageEmojiInteractionSeen,
        types.SendMessageHistoryImportAction, types.SpeakingInGroupCallAction,
    }  # fmt: skip
    every = {
        getattr(types, name)
        for name in dir(types)
        if name.startswith("SendMessage") and isinstance(getattr(types, name), type)
    } | {types.SpeakingInGroupCallAction}
    assert every == known | ignored


async def test_premium_is_noticed(home):
    client = SimpleNamespace(flood_sleep_threshold=0)

    async def get_me():
        return types.User(ME, first_name="Me", premium=True)

    client.get_me = get_me
    backend = TelegramBackend(home.session, 1, "h", client_factory=lambda *a: client)
    backend._client = client
    await backend.me()
    assert backend._premium
    await asyncio.sleep(0)
