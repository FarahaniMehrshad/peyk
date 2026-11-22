"""First run: API keys and an optional proxy."""

from __future__ import annotations

import re
from unicodedata import decimal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static

from peyk.backend import proxy
from peyk.secrets import Secrets
from peyk.ui.widgets.bidi_label import BidiLabel


def ascii_digits(text: str) -> str:
    """Persian and Arabic digits typed on a local layout become 0-9."""
    return "".join(str(decimal(c)) if decimal(c, None) is not None else c for c in text)


def validate(api_id: str, api_hash: str, proxy_text: str) -> Secrets:
    """Raises ValueError with a message for the user."""
    number = ascii_digits(api_id).strip()
    if not re.fullmatch(r"[0-9]{1,10}", number) or int(number) == 0:
        raise ValueError("The API ID is a number, for example 1234567.")
    digest = api_hash.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", digest):
        raise ValueError("The API hash is 32 characters, using only 0-9 and a-f.")
    proxy.parse(proxy_text)
    return Secrets(int(number), digest, proxy_text.strip())


class SetupScreen(Screen[Secrets]):
    BINDINGS = [
        Binding("escape", "cancel", "Back", show=False),
        Binding("down", "app.focus_next", show=False),
        Binding("up", "app.focus_previous", show=False),
    ]

    def __init__(self, current: Secrets | None = None, notice: str = "") -> None:
        super().__init__()
        self._current = current
        self._notice = notice
        self._initial = (
            {"api-id": str(current.api_id), "api-hash": current.api_hash, "proxy": current.proxy}
            if current
            else {"api-id": "", "api-hash": "", "proxy": ""}
        )

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="card", can_focus=False):
            yield Static("Peyk setup", classes="title")
            yield Static(
                "Telegram needs every app to have its own API keys. Get yours at "
                "my.telegram.org, under API development tools. They are encrypted "
                "for your Windows account and stay on this computer.",
                markup=False,
                classes="help",
            )
            yield Static("API ID", classes="label")
            yield Input(self._initial["api-id"], id="api-id", max_length=12)
            yield Static("API hash", classes="label")
            yield Input(
                self._initial["api-hash"], id="api-hash", password=True, max_length=40
            )
            yield Static("Proxy (optional)", classes="label")
            yield Input(
                self._initial["proxy"],
                id="proxy",
                placeholder="socks5://host:port, tg://proxy?..., or system",
            )
            yield BidiLabel(self._notice, id="message", classes="error")
            back = "Esc: back    " if self._current else ""
            yield Static(
                f"Enter: next / save    Up, Down: move    {back}Ctrl+Q: quit",
                markup=False,
                classes="hints",
            )

    def on_mount(self) -> None:
        self.query_one("#api-id", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        # Fields announce their initial value too; that must not hide the notice.
        if event.value != self._initial.get(event.input.id or ""):
            self.query_one("#message", BidiLabel).update("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id != "proxy":
            self.focus_next()
            return
        values = [self.query_one(f"#{name}", Input).value for name in ("api-id", "api-hash",
                                                                       "proxy")]  # fmt: skip
        try:
            secrets = validate(*values)
        except ValueError as error:
            self.query_one("#message", BidiLabel).update(str(error))
            return
        self.dismiss(secrets)

    def action_cancel(self) -> None:
        if self._current is not None:
            self.dismiss(self._current)
