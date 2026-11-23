"""Moves between setup, sign-in and the main screen."""

from __future__ import annotations

import logging
from collections.abc import Callable

from textual.app import App

from peyk import secrets as secret_store
from peyk.backend import proxy as proxies
from peyk.backend.base import Backend
from peyk.backend.session import remove_session
from peyk.config import Config
from peyk.loop import use_lazy_tasks
from peyk.paths import Paths
from peyk.platform.graphics import Graphics
from peyk.platform.notify import Notifier
from peyk.platform.palette import Palette
from peyk.secrets import SecretError, Secrets
from peyk.ui import themes
from peyk.ui.screens.login import LoginScreen
from peyk.ui.screens.main import MainScreen
from peyk.ui.screens.setup import SetupScreen

log = logging.getLogger(__name__)
BackendFactory = Callable[[Secrets, "proxies.Proxy | None"], Backend]


class PeykApp(App[None]):
    TITLE = "Peyk"
    CSS_PATH = "theme.tcss"
    ENABLE_COMMAND_PALETTE = False

    def __init__(
        self,
        config: Config,
        paths: Paths | None,
        factory: BackendFactory | None = None,
        backend: Backend | None = None,
        graphics: Graphics | None = None,
        palette: Palette | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        """With `backend`, run on it directly (demo): nothing is read or stored.

        `palette` is that of the terminal.
        """
        super().__init__()
        self.register_theme(themes.terminal_theme(palette))
        self._config = config
        self._paths = paths
        self._factory = factory
        self._fixed = backend
        self._graphics = graphics
        self._notifier = notifier
        self._backend = backend
        self._secrets: Secrets | None = None

    def on_load(self) -> None:
        use_lazy_tasks()

    def get_css_variables(self) -> dict[str, str]:
        """With our own ones, for the themes that come with Textual."""
        variables = super().get_css_variables()
        for name, same_as in themes.DEFAULTS.items():
            variables.setdefault(name, variables[same_as])
        return variables

    def on_mount(self) -> None:
        if self._config.theme in self.available_themes:
            self.theme = self._config.theme
        else:
            self.theme = themes.TERMINAL
            self.notify(f"Unknown theme '{self._config.theme}' in config.toml", markup=False)
        if self._fixed:
            self.push_screen(LoginScreen(self._fixed))
            return
        assert self._paths
        notice = ""
        try:
            self._secrets = secret_store.load(self._paths.secrets)
        except SecretError:
            notice = "The saved API keys cannot be read on this Windows account. Enter them again."
        if self._secrets:
            self._sign_in()
        else:
            self._setup(notice)

    # -- setup --------------------------------------------------------------

    def _setup(self, notice: str = "") -> None:
        self._replace(SetupScreen(self._secrets, notice), self._setup_done)

    def _setup_done(self, secrets: Secrets | None) -> None:
        if secrets is None or self._paths is None:
            return
        try:
            secret_store.save(self._paths.secrets, secrets)
        except (SecretError, OSError) as error:
            log.warning("could not save secrets", exc_info=error)
            self.call_later(self._setup, "The keys could not be saved on this computer.")
            return
        self._secrets = secrets
        self.call_later(self._sign_in)

    # -- sign in ------------------------------------------------------------

    def _sign_in(self) -> None:
        assert self._secrets and self._factory
        try:
            # config.toml wins over the setup screen; "none" there means direct.
            proxy = proxies.parse(self._config.proxy or self._secrets.proxy)
        except proxies.ProxyError as error:
            self._setup(str(error))
            return
        self._backend = self._factory(self._secrets, proxy)
        self._replace(LoginScreen(self._backend))

    def on_login_screen_signed_in(self) -> None:
        assert self._backend
        self._replace(
            MainScreen(self._backend, self._config, self._graphics, self._notifier)
        )

    async def on_login_screen_edit_setup(self, event: LoginScreen.EditSetup) -> None:
        if self._fixed:
            return
        await self._close_backend()
        self._setup(event.notice)

    async def on_login_screen_forget(self) -> None:
        if self._fixed or not self._paths:
            return
        await self._close_backend()
        remove_session(self._paths.session)
        self._sign_in()

    # -- sign out and exit --------------------------------------------------

    async def on_main_screen_logged_out(self) -> None:
        if self._notifier:
            self._notifier.clear(for_good=True)
        if self._fixed:
            self._replace(LoginScreen(self._fixed))
            return
        await self._close_backend()
        if self._paths:
            secret_store.delete(self._paths.secrets)
        self._secrets = None
        self._setup()

    async def _close_backend(self) -> None:
        backend, self._backend = self._backend, None
        if backend:
            await backend.close()

    async def action_quit(self) -> None:
        """Ctrl+Q: ask first if something is not sent."""
        if isinstance(self.screen, MainScreen):
            self.screen.do_quit()
        else:
            self.exit()

    async def on_unmount(self) -> None:
        if self._notifier:
            self._notifier.close()
        await self._close_backend()

    def _replace(self, screen, callback=None) -> None:
        """Show `screen` as the only one above the empty base screen."""
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.push_screen(screen, callback)
