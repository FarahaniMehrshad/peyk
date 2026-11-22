"""Runs the app on made-up chats and presses keys."""

from peyk.backend.base import BackendError, ErrorKind
from peyk.backend.fake import FakeBackend
from peyk.config import Config
from peyk.ui.app import PeykApp
from peyk.ui.screens.main import MainScreen
from peyk.ui.widgets.conversation import Conversation

OFFLINE = BackendError(ErrorKind.NETWORK, "Could not reach Telegram.")



class Driver:
    def __init__(self, pilot, backend):
        self.pilot, self.backend, self.app = pilot, backend, pilot.app

    @property
    def screen(self) -> MainScreen:
        assert isinstance(self.app.screen, MainScreen)
        return self.app.screen

    @property
    def talk(self) -> Conversation:
        return self.screen.conversation

    async def settle(self, seconds=0.0):
        await self.pilot.pause(seconds)
        await self.app.workers.wait_for_complete()
        await self.pilot.pause()

    async def press(self, *keys):
        await self.pilot.press(*keys)
        await self.settle()

    async def press_now(self, *keys):
        """Without waiting for what they start."""
        await self.pilot.press(*keys)
        await self.pilot.pause()

    async def open(self, chat_id):
        self.screen.open_chat(chat_id)
        await self.settle()

    def lines(self):
        strips = self.app.screen._compositor.render_strips()
        return ["".join(s.text for s in strip) for strip in strips]

    def text(self):
        return "\n".join(self.lines())

    def said(self):
        """The conversation alone, as text."""
        height = self.talk.size.height
        return [self.talk.render_line(y).text for y in range(height)]

    def hold_reading(self):
        self.app.app_focus = False


class run:
    def __init__(
        self, size=(120, 36), backend=None, config=None, graphics=None, palette=None, **options
    ):
        self.backend = backend or FakeBackend(**options)
        self.app = PeykApp(
            config or Config(), None, backend=self.backend, graphics=graphics, palette=palette
        )
        self.context = self.app.run_test(size=size)

    async def __aenter__(self):
        pilot = await self.context.__aenter__()
        driver = Driver(pilot, self.backend)
        await driver.settle()
        await driver.settle()
        return driver

    async def __aexit__(self, *details):
        return await self.context.__aexit__(*details)
