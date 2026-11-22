"""The queue of messages on their way out, without any screen."""

import asyncio

import pytest

from peyk import outbox as outbox_module
from peyk.backend.base import BackendError, ErrorKind
from peyk.models import State
from peyk.outbox import FIRST_ID, Outbox

NETWORK = BackendError(ErrorKind.NETWORK, "Could not reach Telegram.")
REFUSED = BackendError(ErrorKind.REFUSED, "No.")


class Backend:
    def __init__(self):
        self.sends = []
        self.failures = []

    async def send(self, chat_id, text, spans, reply_to, token, uploaded=None):
        self.sends.append((chat_id, text, token))
        await asyncio.sleep(0)
        if self.failures and (failure := self.failures.pop(0)):
            raise failure
        return None


class Rig:
    def __init__(self):
        self.backend = Backend()
        self.tasks = []
        self.sent = []
        self.outbox = Outbox(
            self.backend,
            lambda work: self.tasks.append(asyncio.ensure_future(work)),
            lambda seconds, call: asyncio.get_running_loop().call_later(seconds, call),
            lambda chat_id: None,
            lambda chat_id, message: self.sent.append(chat_id),
        )

    def add(self, text, chat_id=1):
        return self.outbox.add(chat_id, text, (), None, 9, text)

    def texts(self):
        return [text for _, text, _ in self.backend.sends]

    async def settle(self):
        for _ in range(20):
            await asyncio.sleep(0)


@pytest.fixture
async def rig():
    rig = Rig()
    yield rig
    for task in rig.tasks:
        task.cancel()


async def test_order_and_ids(rig):
    first, second = rig.add("a"), rig.add("b")
    assert FIRST_ID <= first.id < second.id and rig.outbox.unsent == 2
    await rig.settle()
    assert rig.texts() == ["a", "b"] and rig.outbox.unsent == 0


async def test_network_trouble_is_retried_even_if_the_link_stays_up(rig, monkeypatch):
    monkeypatch.setattr(outbox_module, "FIRST_PAUSE", 0.05)
    rig.backend.failures = [NETWORK, NETWORK]
    rig.add("a")
    rig.add("b")
    await rig.settle()
    assert rig.texts() == ["a"]
    await asyncio.sleep(0.4)
    assert rig.texts() == ["a", "a", "a", "b"] and rig.outbox.unsent == 0
    assert len({token for _, text, token in rig.backend.sends if text == "a"}) == 1


async def test_pauses_grow_and_do_not_pile_up(rig, monkeypatch):
    pauses = []

    monkeypatch.setattr(rig.outbox, "_later", lambda seconds, call: pauses.append(seconds))
    rig.backend.failures = [NETWORK] * 7
    rig.add("a")
    await rig.settle()
    for _ in range(6):
        rig.outbox.resume()
        await rig.settle()
    assert pauses == [5, 10, 20, 40, 60, 60, 60]


async def test_a_pause_is_dropped_when_it_was_tried_meanwhile(rig, monkeypatch):
    monkeypatch.setattr(outbox_module, "FIRST_PAUSE", 0.05)
    monkeypatch.setattr(outbox_module, "LONGEST_PAUSE", 0.2)
    rig.backend.failures = [NETWORK, BackendError(ErrorKind.FLOOD, "Slow.", 30)]
    rig.add("a")
    await rig.settle()
    rig.outbox.resume()
    await rig.settle()
    await asyncio.sleep(0.15)  # the first pause ends; the wait of 30 s still holds
    assert rig.texts() == ["a", "a"]


async def test_a_refused_message_does_not_hold_up_the_next(rig):
    rig.backend.failures = [REFUSED]
    message = rig.add("a")
    rig.add("b")
    await rig.settle()
    assert rig.texts() == ["a", "b"]
    assert rig.outbox.get(1, message.id).state is State.FAILED
    rig.outbox.resume()
    await rig.settle()
    assert rig.texts() == ["a", "b"]  # only when asked
    rig.outbox.retry(1, message.id)
    await rig.settle()
    assert rig.texts() == ["a", "b", "a"] and rig.outbox.unsent == 0


async def test_discarding_a_waiting_message_frees_the_next(rig):
    rig.backend.failures = [NETWORK]
    message = rig.add("a")
    rig.add("b")
    await rig.settle()
    assert rig.outbox.take_back(1, message.id) == ("a", None, None)
    await rig.settle()
    assert rig.texts() == ["a", "b"] and rig.outbox.unsent == 0


async def test_too_late_to_discard(rig):
    message = rig.add("a")
    await asyncio.sleep(0)
    assert rig.outbox.discard(1, message.id) is None


async def test_chats_do_not_wait_for_each_other(rig):
    rig.backend.failures = [NETWORK]
    rig.add("a", chat_id=1)
    await rig.settle()
    rig.add("b", chat_id=2)
    await rig.settle()
    assert rig.sent == [2]
