"""Telethon objects to models, using real Telethon types."""

from datetime import UTC, datetime, timedelta

import pytest
from telethon import types
from telethon.tl import custom

from peyk.backend import convert
from peyk.models import FOREVER, Kind, Reply

ME = 1
DATE = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
USER = types.User(id=5, first_name="Sara", last_name="M", access_hash=1, contact=True)
MYSELF = types.User(id=ME, first_name="Me", is_self=True, access_hash=1)
GROUP = types.Channel(id=30, title="Devs", photo=None, date=DATE, megagroup=True, access_hash=1)
NEWS = types.Channel(id=31, title="News", photo=None, date=DATE, broadcast=True, access_hash=1)
BASIC = types.Chat(id=40, title="Basic", photo=None, participants_count=3, date=DATE, version=1)


class Client:
    _self_id = ME
    _mb_entity_cache = {}


def raw(text="hi", **extra):
    extra.setdefault("peer_id", types.PeerUser(5))
    extra.setdefault("from_id", types.PeerUser(5))
    message = types.Message(id=7, date=DATE, message=text, **extra)
    entities = {5: USER, ME: MYSELF, -1_000_000_000_030: GROUP}
    message._finish_init(Client, entities, None)
    return message


def service(action, **extra):
    message = types.MessageService(id=7, peer_id=types.PeerUser(5), date=DATE, action=action,
                                   from_id=types.PeerUser(5), **extra)  # fmt: skip
    message._finish_init(Client, {5: USER, ME: MYSELF}, None)
    return message


# -- entity offsets


def spans(text, *entities):
    return convert.text_and_spans(text, entities)


def test_offsets_after_emoji_count_utf16_units():
    text, (span,) = spans("👍🏽 bold", types.MessageEntityBold(5, 4))
    assert text[span.start : span.end] == "bold"


def test_offsets_after_removed_characters():
    text, (span,) = spans("\x1b\x00a\tbold", types.MessageEntityBold(4, 4))
    assert text == "a    bold" and text[span.start : span.end] == "bold"


def test_offsets_after_crlf():
    text, (span,) = spans("a\r\nbold", types.MessageEntityBold(3, 4))
    assert text[span.start : span.end] == "bold"


@pytest.mark.parametrize(("offset", "length"), [(-5, 3), (0, 999), (999, 5), (3, 0), (5, -2)])
def test_nonsense_offsets_do_not_crash(offset, length):
    text, found = spans("hello", types.MessageEntityBold(offset, length))
    assert all(0 <= s.start < s.end <= len(text) for s in found)


def test_unknown_entities_are_ignored_and_urls_cleaned():
    text, found = spans("x link", types.MessageEntityCustomEmoji(0, 1, 5),
                        types.MessageEntityTextUrl(2, 4, "https://e.x/\x1b[31m"))  # fmt: skip
    assert [(s.kind, s.url) for s in found] == [("link", "https://e.x/[31m")]


# -- messages


def test_plain_message():
    m = convert.message(raw("سلام\x1b[2J", out=True, edit_date=DATE), 5)
    assert m.text == "سلام[2J" and m.sender == "Sara M" and m.outgoing and m.edited
    assert m.date == DATE and m.chat_id == 5 and m.sender_id == 5


def test_hidden_edits_do_not_show():
    assert not convert.message(raw(edit_date=DATE, edit_hide=True), 5).edited


def test_replies():
    header = types.MessageReplyHeader(reply_to_msg_id=3)
    assert convert.message(raw(reply_to=header), 5).reply == Reply(3)
    quoted = types.MessageReplyHeader(reply_to_msg_id=3, quote_text="part\nof")
    assert convert.message(raw(reply_to=quoted), 5).reply == Reply(3, "", "part of")


def test_topics_and_other_chats_are_not_replies():
    topic = types.MessageReplyHeader(reply_to_msg_id=3, forum_topic=True)
    elsewhere = types.MessageReplyHeader(reply_to_msg_id=3, reply_to_peer_id=types.PeerUser(9))
    in_topic = types.MessageReplyHeader(reply_to_msg_id=4, forum_topic=True, reply_to_top_id=3)
    assert convert.message(raw(reply_to=topic), 5).reply is None
    assert convert.message(raw(reply_to=elsewhere), 5).reply is None
    assert convert.message(raw(reply_to=in_topic), 5).reply.id == 4


def test_forward_names():
    hidden = types.MessageFwdHeader(date=DATE, from_name="Hidden \x07Person")
    assert convert.message(raw(fwd_from=hidden), 5).forwarded == "Hidden Person"
    nobody = types.MessageFwdHeader(date=DATE)
    assert convert.message(raw(fwd_from=nobody), 5).forwarded == "unknown"


def document(name="a.bin", *attributes, mime="application/octet-stream"):
    attributes = [types.DocumentAttributeFilename(name), *attributes]
    doc = types.Document(id=1, access_hash=1, file_reference=b"", date=DATE, mime_type=mime,
                         size=1_300_000, dc_id=1, attributes=attributes)  # fmt: skip
    return types.MessageMediaDocument(document=doc)


def question(text):
    return types.TextWithEntities(text, [])


POLL = types.MessageMediaPoll(
    poll=types.Poll(
        id=1, question=question("Tea?"), hash=0,
        answers=[types.PollAnswer(question("Yes"), b"1"), types.PollAnswer(question("No"), b"2")],
    ),
    results=types.PollResults(),
)  # fmt: skip


@pytest.mark.parametrize(
    ("media", "label"),
    [
        (
            types.MessageMediaPhoto(
                photo=types.Photo(
                    1, 1, b"", DATE, [types.PhotoSize("x", 640, 480, 1000)], dc_id=1
                )
            ),
            "Photo 640x480",
        ),
        (document("report\x1b.pdf"), "File report.pdf (1.2 MB)"),
        (
            document("v.ogg", types.DocumentAttributeAudio(65, voice=True), mime="audio/ogg"),
            "Voice message 1:05",
        ),
        (
            document("v.mp4", types.DocumentAttributeVideo(3700, 10, 10), mime="video/mp4"),
            "Video 1:01:40",
        ),
        (types.MessageMediaGeo(types.GeoPoint(1, 2, 3)), "Location"),
        (types.MessageMediaContact("+1", "Ali", "R", "", 0), "Contact Ali R"),
        (types.MessageMediaDice(6, "🎲"), "🎲 6"),
        (POLL, "Poll: Tea?"),
        (types.MessageMediaUnsupported(), "Unsupported content"),
        (types.MessageMediaPhoto(), "Photo (expired)"),
    ],
)
def test_media_labels(media, label):
    assert convert.message(raw("", media=media), 5).media.label == label


def test_web_previews_are_not_media():
    page = types.MessageMediaWebPage(types.WebPageEmpty(1))
    assert convert.message(raw("https://x.y", media=page), 5).media is None


def test_poll_options_are_listed():
    assert convert.message(raw("", media=POLL), 5).text == "- Yes\n- No"


@pytest.mark.parametrize(
    ("action", "text"),
    [
        (types.MessageActionChatCreate("T", [5]), "Sara M created the group T"),
        (types.MessageActionChatEditTitle("New\x1b"), "Sara M renamed the chat to New"),
        (types.MessageActionPinMessage(), "Sara M pinned a message"),
        (types.MessageActionChatJoinedByLink(5), "Sara M joined"),
        (types.MessageActionContactSignUp(), "Sara M joined Telegram"),
        (types.MessageActionPhoneCall(1, duration=61), "Incoming call, 1:01"),
        (
            types.MessageActionPhoneCall(1, reason=types.PhoneCallDiscardReasonMissed()),
            "Missed call",
        ),
        (types.MessageActionCustomAction("Hello"), "Hello"),
        (types.MessageActionBoostApply(1), "Service message"),
    ],
)
def test_service_messages(action, text):
    m = convert.message(service(action), 5)
    assert m.service and m.text == text


# -- chats and folders


def dialog(entity, peer, **extra):
    fields = dict(top_message=7, read_inbox_max_id=3, read_outbox_max_id=4, unread_count=2,
                  unread_mentions_count=1, unread_reactions_count=0, unread_poll_votes_count=0,
                  notify_settings=types.PeerNotifySettings())  # fmt: skip
    data = types.Dialog(peer=peer, **(fields | extra))
    from telethon import utils

    return custom.Dialog(None, data, {utils.get_peer_id(peer): entity}, raw())


def test_dialog_to_chat():
    chat = convert.chat_from_dialog(dialog(USER, types.PeerUser(5), pinned=True), ME, {}, 2)
    assert (chat.id, chat.title, chat.kind, chat.unread, chat.mentions) == (
        5, "Sara M", Kind.USER, 2, 1,
    )  # fmt: skip
    assert chat.pin == 2 and chat.contact and (chat.read_inbox, chat.read_outbox) == (3, 4)
    assert chat.last.text == "hi"


@pytest.mark.parametrize(
    ("entity", "kind"),
    [
        (USER, Kind.USER), (MYSELF, Kind.SAVED), (GROUP, Kind.GROUP), (NEWS, Kind.CHANNEL),
        (BASIC, Kind.GROUP), (types.User(id=9, bot=True, first_name="B"), Kind.BOT),
        (types.ChannelForbidden(id=1, access_hash=1, title="x", broadcast=True), Kind.CHANNEL),
        (types.ChatForbidden(1, "x"), Kind.GROUP),
    ],
)  # fmt: skip
def test_kinds(entity, kind):
    assert convert.kind_of(entity, ME) is kind


def test_names():
    assert convert.name_of(types.User(id=1, deleted=True)) == "Deleted account"
    saved = convert.chat_from_entity(MYSELF, ME, {})
    assert saved.title == "Saved Messages"
    assert convert.chat_from_entity(types.User(id=9, access_hash=1), ME, {}).title == "Unnamed chat"
    assert convert.chat_from_entity(object(), ME, {}) is None


def test_muting():
    later = datetime.now(UTC) + timedelta(hours=1)
    settings = types.PeerNotifySettings(mute_until=later)
    assert convert.muted_until(settings) == pytest.approx(later.timestamp(), abs=1)
    forever = types.PeerNotifySettings(mute_until=datetime(2038, 1, 19, 3, 14, 7, tzinfo=UTC))
    assert convert.muted_until(forever) == FOREVER
    assert convert.muted_until(types.PeerNotifySettings(), 5.0) == 5.0
    chat = convert.chat_from_dialog(dialog(USER, types.PeerUser(5)), ME, {Kind.USER: FOREVER}, 0)
    assert chat.muted(0)


def test_archived_and_marked_unread():
    chat = convert.chat_from_dialog(
        dialog(USER, types.PeerUser(5), folder_id=1, unread_mark=True), ME, {}, 0
    )
    assert chat.archived and chat.marked_unread


def test_folders():
    data = types.DialogFilter(
        id=2, title=question("Work\x1b"), pinned_peers=[types.InputPeerUser(5, 1)],
        include_peers=[types.InputPeerChannel(30, 1), types.InputPeerSelf()],
        exclude_peers=[types.InputPeerChat(40)], groups=True, exclude_muted=True,
    )  # fmt: skip
    folder = convert.folder(data, ME)
    assert folder.title == "Work" and folder.pinned == (5,)
    assert folder.include == {-1_000_000_000_030, ME} and folder.exclude == {-40}
    assert folder.groups and folder.exclude_muted and not folder.bots
    shared = types.DialogFilterChatlist(id=3, title=question("S"), pinned_peers=[],
                                        include_peers=[types.InputPeerUser(5, 1)])  # fmt: skip
    assert convert.folder(shared, ME).include == {5}
    assert convert.folder(types.DialogFilterDefault(), ME) is None


# -- who may write


def channel(**extra):
    return types.Channel(id=30, title="x", photo=None, date=DATE, access_hash=1, **extra)


NO_SENDING = types.ChatBannedRights(until_date=None, send_messages=True)
ADMIN = types.ChatAdminRights(post_messages=True)


@pytest.mark.parametrize(
    ("entity", "kind", "allowed"),
    [
        (USER, Kind.USER, True),
        (types.User(id=5, deleted=True), Kind.USER, False),
        (types.User(id=777000, first_name="Telegram"), Kind.USER, False),
        (BASIC, Kind.GROUP, True),
        (types.ChatForbidden(40, "x"), Kind.GROUP, False),
        (channel(broadcast=True), Kind.CHANNEL, False),
        (channel(broadcast=True, admin_rights=ADMIN), Kind.CHANNEL, True),
        (channel(broadcast=True, creator=True), Kind.CHANNEL, True),
        (channel(megagroup=True), Kind.GROUP, True),
        (channel(megagroup=True, default_banned_rights=NO_SENDING), Kind.GROUP, False),
        (channel(megagroup=True, banned_rights=NO_SENDING), Kind.GROUP, False),
        (
            channel(megagroup=True, default_banned_rights=NO_SENDING, admin_rights=ADMIN),
            Kind.GROUP,
            True,
        ),
    ],
)
def test_who_may_write(entity, kind, allowed):
    assert convert.can_write(entity, kind) is allowed
