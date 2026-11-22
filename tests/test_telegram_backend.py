"""The real backend against a stand-in for Telethon's client."""

import asyncio
from datetime import UTC, datetime

import pytest
from telethon import errors, functions, types
from telethon.tl import custom

from peyk.backend import telegram
from peyk.backend.base import (
    AuthStep,
    BackendError,
    ChatFlags,
    ErrorKind,
    FoldersChanged,
    InboxRead,
    Link,
    MessageAdded,
    MessageChanged,
    MessagesRemoved,
    OutboxRead,
    PinsChanged,
)
from peyk.backend.proxy import parse
from peyk.backend.session import EncryptedSession
from peyk.backend.telegram import TelegramBackend, make_client, translate


class StubClient:
    def __init__(self):
        self.replies = {}
        self.requests = []
        self.handlers = []
        self.emitted = []
        self.connect_errors = []
        self.sign_in_errors = []
        self.disconnected = asyncio.get_running_loop().create_future()
        self.up = True
        self.logged_out = True
        self.connects = 0
        self._sender = self

    def _transport_connected(self):
        return self.up

    def is_connected(self):
        return not self.disconnected.done()

    def add_event_handler(self, handler, kind):
        self.handlers.append((handler, kind))

    async def connect(self):
        self.connects += 1
        if self.connect_errors:
            raise self.connect_errors.pop(0)
        self.disconnected = asyncio.get_running_loop().create_future()

    async def disconnect(self):
        if not self.disconnected.done():
            self.disconnected.set_result(None)

    async def __call__(self, request):
        self.requests.append(request)
        reply = self.replies.get(type(request))
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def sign_in(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        if self.sign_in_errors:
            raise self.sign_in_errors.pop(0)

    async def get_me(self):
        return types.User(id=5, first_name="سارا\x1b[31m", last_name=None, username="s")

    async def log_out(self):
        if self.logged_out is None:
            await asyncio.sleep(60)
        return self.logged_out


def rpc(kind, *args):
    return kind(None, *args)


def sent(kind=None, **extra):
    return types.auth.SentCode(type=kind or types.auth.SentCodeTypeApp(5), phone_code_hash="H",
                               **extra)  # fmt: skip


@pytest.fixture
async def rig(home):
    client = StubClient()
    client.replies[functions.updates.GetStateRequest] = rpc(errors.AuthKeyUnregisteredError)
    backend = TelegramBackend(home.session, 1, "h", client_factory=lambda *a: client)
    yield backend, client
    await backend.close()


async def test_new_account_flow(rig):
    backend, client = rig
    links = []
    backend.on_link(links.append)
    assert await backend.connect() is AuthStep.PHONE
    assert links == [Link.CONNECTING, Link.ONLINE]
    client.replies[functions.auth.SendCodeRequest] = sent(next_type=types.auth.CodeTypeSms())
    code = await backend.send_code("+447700900123")
    assert (code.where, code.length, code.can_resend) == ("to your other Telegram apps", 5, True)
    client.sign_in_errors = [rpc(errors.SessionPasswordNeededError)]
    assert await backend.submit_code("12345") is AuthStep.PASSWORD
    assert client.requests[-1] == (("+447700900123", "12345"), {"phone_code_hash": "H"})
    assert await backend.submit_password("pw") is AuthStep.READY
    me = await backend.me()
    assert me.name == "سارا[31m" and "\x1b" not in me.name


async def test_resend_uses_the_latest_hash(rig):
    backend, client = rig
    await backend.connect()
    client.replies[functions.auth.SendCodeRequest] = sent()
    client.replies[functions.auth.ResendCodeRequest] = sent(types.auth.SentCodeTypeSms(5))
    await backend.send_code("+447700900123")
    assert (await backend.resend_code()).where == "by SMS"
    request = client.requests[-1]
    assert (request.phone_number, request.phone_code_hash) == ("+447700900123", "H")


async def test_already_signed_in(rig):
    backend, client = rig
    client.replies[functions.updates.GetStateRequest] = object()
    assert await backend.connect() is AuthStep.READY


async def test_server_trouble_is_not_mistaken_for_signed_out(rig):
    backend, client = rig
    client.replies[functions.updates.GetStateRequest] = errors.ServerError(None, "oops", 500)
    with pytest.raises(BackendError) as error:
        await backend.connect()
    assert error.value.kind is ErrorKind.OTHER
    assert backend.link is Link.OFFLINE


async def test_auth_restart_is_retried_once(rig):
    backend, client = rig
    await backend.connect()
    client.replies[functions.auth.SendCodeRequest] = [rpc(errors.AuthRestartError), sent()]
    assert (await backend.send_code("+447700900123")).length == 5


async def test_unsupported_login_is_explained(rig):
    backend, client = rig
    await backend.connect()
    kind = types.auth.SentCodeTypeSetUpEmailRequired()
    client.replies[functions.auth.SendCodeRequest] = sent(kind)
    with pytest.raises(BackendError, match="official app"):
        await backend.send_code("+447700900123")


async def test_failed_connect_can_be_retried(rig):
    backend, client = rig
    client.connect_errors = [ConnectionError("no")]
    with pytest.raises(BackendError) as error:
        await backend.connect()
    assert error.value.kind is ErrorKind.NETWORK
    assert await backend.connect() is AuthStep.PHONE


async def test_proxy_failure_names_the_proxy(home):
    client = StubClient()
    client.connect_errors = [ConnectionError("no")]
    proxy = parse("socks5://user:hunter2@host:1")
    backend = TelegramBackend(home.session, 1, "h", proxy, client_factory=lambda *a: client)
    with pytest.raises(BackendError) as error:
        await backend.connect()
    assert error.value.message == "Could not reach Telegram through SOCKS5 host:1."


async def fast_sleep(monkeypatch):
    real = asyncio.sleep
    monkeypatch.setattr(telegram.asyncio, "sleep", lambda s: real(0.001 if s else 0))
    return real


async def test_link_follows_the_transport(rig, monkeypatch):
    backend, client = rig
    sleep = await fast_sleep(monkeypatch)
    await backend.connect()
    client.up = False
    await sleep(0.05)
    assert backend.link is Link.CONNECTING
    client.up = True
    await sleep(0.05)
    assert backend.link is Link.ONLINE


async def test_reconnects_after_telethon_gives_up(rig, monkeypatch):
    backend, client = rig
    sleep = await fast_sleep(monkeypatch)
    links = []
    await backend.connect()
    backend.on_link(links.append)
    client.connect_errors = [ConnectionError(), OSError()]
    client.disconnected.set_exception(ConnectionError())
    await sleep(0.2)
    assert client.connects == 4 and backend.link is Link.ONLINE
    assert links[0] is Link.OFFLINE and links[-1] is Link.ONLINE


async def test_data_centre_move_is_not_a_disconnect(rig, monkeypatch):
    backend, client = rig
    sleep = await fast_sleep(monkeypatch)
    await backend.connect()
    links = []
    backend.on_link(links.append)
    monkeypatch.setattr(client, "is_connected", lambda: True)
    old, client.disconnected = client.disconnected, asyncio.get_running_loop().create_future()
    old.set_result(None)
    await sleep(0.05)
    assert links == [] and client.connects == 1


async def test_logout_deletes_the_session_only_after_revoking(rig, home):
    backend, client = rig
    await backend.connect()
    EncryptedSession(home.session).close()

    backend._set_link(Link.OFFLINE)
    with pytest.raises(BackendError, match="online"):
        await backend.log_out()
    assert home.session.exists()

    backend._set_link(Link.ONLINE)
    client.logged_out = False
    with pytest.raises(BackendError, match="confirm"):
        await backend.log_out()
    assert home.session.exists()

    client.logged_out = None
    monkey = telegram.CALL_TIMEOUT
    telegram.CALL_TIMEOUT = 0.05
    try:
        with pytest.raises(BackendError) as error:
            await backend.log_out()
        assert error.value.kind is ErrorKind.NETWORK and home.session.exists()
    finally:
        telegram.CALL_TIMEOUT = monkey

    client.logged_out = True
    await backend.log_out()
    assert not home.session.exists()


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (rpc(errors.PhoneNumberInvalidError), ErrorKind.PHONE),
        (rpc(errors.PhoneNumberBannedError), ErrorKind.ACCOUNT),
        (rpc(errors.PhoneCodeInvalidError), ErrorKind.CODE),
        (rpc(errors.PhoneCodeExpiredError), ErrorKind.CODE_EXPIRED),
        (rpc(errors.PasswordHashInvalidError), ErrorKind.PASSWORD),
        (rpc(errors.ApiIdInvalidError), ErrorKind.API_KEYS),
        (rpc(errors.AuthKeyDuplicatedError), ErrorKind.SESSION),
        (rpc(errors.SendCodeUnavailableError), ErrorKind.CODE),
        (rpc(errors.PhonePasswordFloodError), ErrorKind.FLOOD),
        (TimeoutError(), ErrorKind.NETWORK),
        (ConnectionResetError(), ErrorKind.NETWORK),
        (asyncio.IncompleteReadError(b"", 4), ErrorKind.NETWORK),
        (RuntimeError("secret detail"), ErrorKind.OTHER),
    ],
)
def test_translation(error, kind):
    translated = translate(error)
    assert translated.kind is kind
    assert "secret detail" not in translated.message


def test_flood_wait_carries_the_time():
    error = translate(errors.FloodWaitError(None, capture=90))
    assert error.kind is ErrorKind.FLOOD and error.retry_after == 90


async def test_client_options(home):
    client = make_client(home.session, 1, "h" * 32, parse("socks5://u:p@h:1"))
    assert client._proxy == ("socks5", "h", 1, True, "u", "p")
    assert client._init_request.device_model == "Peyk"
    assert client._init_request.system_version == "Windows"
    client.session.close()
    secret = "ee" + "1" * 30
    client = make_client(home.session, 1, "h" * 32, parse(f"tg://proxy?server=h&port=1&secret={secret}"))
    assert client._connection.normalize_secret(client._proxy[2]).hex() == secret
    client.session.close()


# -- chats, history and live updates


DATE = datetime(2026, 11, 22, tzinfo=UTC)
SARA = types.User(id=5, first_name="Sara", access_hash=1)


class Owner:
    _self_id = 1
    _mb_entity_cache = {}


def raw(i, text="hi", reply=None):
    header = types.MessageReplyHeader(reply_to_msg_id=reply) if reply else None
    message = types.Message(id=i, peer_id=types.PeerUser(5), from_id=types.PeerUser(5), date=DATE,
                            message=text, reply_to=header)  # fmt: skip
    message._finish_init(Owner, {5: SARA}, None)
    return message


class History(StubClient):
    def __init__(self, messages):
        super().__init__()
        self.store = {m.id: m for m in messages}
        self.asked = []
        self.missing_fails = False

    async def get_messages(self, chat_id, limit=None, offset_id=0, ids=None):
        self.asked.append((limit, offset_id, ids))
        if ids is not None:
            if self.missing_fails:
                raise rpc(errors.ChannelPrivateError)
            return [self.store.get(i) for i in ids]
        found = [m for i, m in sorted(self.store.items(), reverse=True)
                 if not offset_id or i < offset_id]  # fmt: skip
        return found[:limit]


def with_client(home, client):
    backend = TelegramBackend(home.session, 1, "h", client_factory=lambda *a: client)
    backend._client = client
    return backend


async def test_history_is_oldest_first_and_pages(home):
    backend = with_client(home, History([raw(i) for i in range(1, 8)]))
    assert [m.id for m in await backend.history(5, limit=3)] == [5, 6, 7]
    assert [m.id for m in await backend.history(5, before=5, limit=3)] == [2, 3, 4]
    assert [m.id for m in await backend.history(5, before=2, limit=3)] == [1]


async def test_replies_outside_the_page_are_fetched_once(home):
    client = History([raw(1, "the original"), raw(8, reply=1), raw(9, reply=8), raw(10, reply=3)])
    messages = await with_client(home, client).history(5, limit=3)
    assert [m.reply.text for m in messages] == ["the original", "hi", "Deleted message"]
    assert messages[0].reply.name == "Sara"
    assert client.asked[1:] == [(None, 0, [1, 3])]


async def test_unavailable_replies_do_not_break_history(home):
    client = History([raw(8, reply=1)])
    client.missing_fails = True
    (message,) = await with_client(home, client).history(5)
    assert message.reply.text == "Deleted message"


class Happening:
    """Stands in for a Telethon event."""

    def __init__(self, message=None, chat_id=5, deleted_ids=()):
        self.message, self.chat_id, self.deleted_ids = message, chat_id, deleted_ids

    async def get_sender(self):
        raise ValueError("not cached")

    async def get_chat(self):
        return SARA


async def test_live_events(home):
    client = History([])
    backend = with_client(home, client)
    seen = []
    backend.on_event(seen.append)
    backend._subscribe(client)
    new, edited, deleted, _ = (handler for handler, _ in client.handlers)
    await new(Happening(raw(1)))
    await new(Happening(raw(2)))
    await edited(Happening(raw(2, "changed")))
    await deleted(Happening(chat_id=None, deleted_ids=[1, 2]))
    assert isinstance(seen[0], MessageAdded) and seen[0].chat.title == "Sara"
    assert seen[1].chat is None  # already known
    assert isinstance(seen[2], MessageChanged) and seen[2].message.text == "changed"
    assert seen[3] == MessagesRemoved(None, (1, 2))


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        (
            types.UpdateReadHistoryInbox(types.PeerUser(5), 9, 3, 1, 1),
            InboxRead(5, 9, 3),
        ),
        (
            types.UpdateReadChannelInbox(30, 9, 0, 1),
            InboxRead(-1_000_000_000_030, 9, 0),
        ),
        (types.UpdateReadHistoryOutbox(types.PeerChat(4), 9, 1, 1), OutboxRead(-4, 9)),
        (types.UpdateReadChannelOutbox(30, 9), OutboxRead(-1_000_000_000_030, 9)),
        (
            types.UpdateDialogUnreadMark(types.DialogPeer(types.PeerUser(5)), unread=True),
            ChatFlags(5, marked_unread=True),
        ),
        (
            types.UpdateNotifySettings(
                types.NotifyPeer(types.PeerUser(5)), types.PeerNotifySettings()
            ),
            ChatFlags(5, muted_until=0.0),
        ),
        (
            types.UpdateFolderPeers([types.FolderPeer(types.PeerUser(5), 1)], 1, 1),
            ChatFlags(5, archived=True),
        ),
        (types.UpdatePinnedDialogs(), PinsChanged()),
        (types.UpdateDialogFilter(2), FoldersChanged()),
        (types.UpdateUserStatus(5, types.UserStatusEmpty()), None),
    ],
)
async def test_raw_updates(home, update, expected):
    backend = with_client(home, StubClient())
    seen = []
    backend.on_event(seen.append)
    await backend._on_raw(update)
    assert seen == ([expected] if expected else [])


async def test_a_bad_update_is_contained(home, caplog):
    client = StubClient()
    backend = with_client(home, client)
    backend._subscribe(client)
    handler = client.handlers[0][0]
    await handler(None)  # must not raise
    assert "update not handled" in caplog.text


async def test_chats_come_in_batches_with_pins_numbered(home, monkeypatch):
    monkeypatch.setattr(telegram, "BATCH", 2)
    settings = types.PeerNotifySettings()

    def dialog(i, pinned):
        user = types.User(id=i, first_name=f"U{i}", access_hash=1)
        data = types.Dialog(types.PeerUser(i), 1, 0, 0, 0, 0, 0, 0, settings, pinned=pinned)
        return custom.Dialog(None, data, {i: user}, raw(i))

    class Dialogs(StubClient):
        async def iter_dialogs(self, limit, ignore_migrated):
            for i in range(1, 6):
                yield dialog(i, pinned=i <= 2)

    client = Dialogs()
    client.replies[functions.account.GetNotifySettingsRequest] = settings
    batches = [b async for b in with_client(home, client).chats()]
    assert [[c.id for c in b] for b in batches] == [[1, 2], [3, 4], [5]]
    assert [c.pin for b in batches for c in b] == [1, 2, 0, 0, 0]


async def test_folder_list(home):
    client = StubClient()
    title = types.TextWithEntities("Work", [])
    client.replies[functions.messages.GetDialogFiltersRequest] = types.messages.DialogFilters(
        [types.DialogFilterDefault(), types.DialogFilter(2, title, [], [], [])]
    )
    assert [f.title for f in await with_client(home, client).folders()] == ["Work"]
