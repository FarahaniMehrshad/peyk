"""Textual runs real apps with eager tasks, which silently break Telethon.

run_test() does not, so these tests recreate what App.run() does.
"""

import asyncio

import pytest
from telethon import functions
from telethon.crypto import AuthKey
from telethon.network.mtprotosender import MTProtoSender

from peyk.backend.base import AuthStep
from peyk.backend.fake import FakeBackend
from peyk.backend.telegram import TelegramBackend
from peyk.config import Config
from peyk.loop import use_lazy_tasks
from peyk.ui.app import PeykApp


class Wire:
    """A connection that records what is sent and never answers."""

    def __init__(self):
        self.sent = []

    async def connect(self, timeout=None, ssl=None):
        pass

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        await asyncio.sleep(60)

    async def disconnect(self):
        pass


class Loggers(dict):
    def __missing__(self, name):
        import logging

        return logging.getLogger("telethon")


async def bytes_sent_by_telethon():
    sender = MTProtoSender(AuthKey(data=bytes(range(256))), loggers=Loggers())
    wire = Wire()
    await sender.connect(wire)
    sender.send(functions.help.GetConfigRequest())
    await asyncio.sleep(0.1)
    await sender.disconnect()
    return len(wire.sent)


async def test_the_problem_exists_in_this_telethon():
    """If this starts failing, Telethon fixed it and loop.py can go."""
    asyncio.get_running_loop().set_task_factory(asyncio.eager_task_factory)
    assert await bytes_sent_by_telethon() == 0


async def test_lazy_tasks_fix_it():
    asyncio.get_running_loop().set_task_factory(asyncio.eager_task_factory)
    use_lazy_tasks()
    assert await bytes_sent_by_telethon() > 0


async def test_backend_never_connects_with_eager_tasks(home):
    loop = asyncio.get_running_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    seen = []

    def factory(*_):
        seen.append(loop.get_task_factory())
        raise RuntimeError

    with pytest.raises(Exception, match="RuntimeError"):
        await TelegramBackend(home.session, 1, "h", client_factory=factory).connect()
    assert seen == [None]


async def test_app_started_for_real_uses_lazy_tasks():
    """run_async is what App.run() uses, unlike run_test."""
    seen = []

    class Probe(FakeBackend):
        async def connect(self) -> AuthStep:
            seen.append(asyncio.get_running_loop().get_task_factory())
            asyncio.get_running_loop().call_later(0.1, app.exit)
            return await super().connect()

    app = PeykApp(Config(), None, backend=Probe())
    await app.run_async(headless=True)
    assert seen == [None]
