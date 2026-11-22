"""Connect, then phone number, code and (if set) password."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Awaitable
from enum import Enum, auto

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Input, Static

from peyk.backend.base import AuthStep, Backend, BackendError, CodeSent, ErrorKind
from peyk.ui.screens.setup import ascii_digits
from peyk.ui.widgets.bidi_label import BidiLabel


class Stage(Enum):
    CONNECTING = auto()
    FAILED = auto()
    PHONE = auto()
    CODE = auto()
    PASSWORD = auto()


def clean_phone(text: str) -> str:
    """Raises ValueError with a message for the user."""
    phone = re.sub(r"[\s().\-]", "", ascii_digits(text))
    phone = "+" + phone[2:] if phone.startswith("00") else phone
    if not re.fullmatch(r"\+[0-9]{7,15}", phone):
        raise ValueError("Start with + and the country code, for example +44 7700 900123.")
    return phone


class LoginScreen(Screen[None]):
    class SignedIn(Message):
        pass

    class EditSetup(Message):
        def __init__(self, notice: str = "") -> None:
            super().__init__()
            self.notice = notice

    class Forget(Message):
        """The saved login is unusable; delete it and start over."""

    BINDINGS = [
        Binding("escape", "back", show=False),
        Binding("ctrl+r", "again", show=False),
        Binding("ctrl+s", "setup", show=False),
        Binding("ctrl+d", "forget", show=False),
    ]

    def __init__(self, backend: Backend) -> None:
        super().__init__()
        self._backend = backend
        self.stage = Stage.CONNECTING
        self._busy = False
        self._error: BackendError | None = None
        self._deadline = 0.0
        self._ticker: Timer | None = None
        self._progress: Timer | None = None
        self._connecting_since = 0.0
        self._sent: CodeSent | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="card", can_focus=False):
            yield Static("Sign in to Telegram", classes="title")
            yield BidiLabel(id="prompt", classes="help")
            yield Input(id="field")
            yield BidiLabel(id="message", classes="error")
            yield Static("", id="hints", markup=False, classes="hints")

    def on_mount(self) -> None:
        self._connect()

    # -- what is on screen --------------------------------------------------

    def _show(self, stage: Stage, prompt: str, hints: str) -> None:
        self.stage = stage
        field = self.query_one("#field", Input)
        field.display = stage in (Stage.PHONE, Stage.CODE, Stage.PASSWORD)
        field.password = stage is Stage.PASSWORD
        field.value = ""
        field.placeholder = {Stage.PHONE: "+44 7700 900123", Stage.CODE: "code"}.get(stage, "")
        self.query_one("#prompt", BidiLabel).update(prompt)
        self.query_one("#hints", Static).update(hints)
        self._message("")
        if field.display:
            field.focus()

    def _message(self, text: str, error: bool = True) -> None:
        label = self.query_one("#message", BidiLabel)
        label.set_class(error, "error")
        label.update(text)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.query_one("#field", Input).disabled = busy
        if busy:
            self._message(text, error=False)

    @property
    def _wait(self) -> int:
        """Seconds Telegram still wants us to wait. A deadline survives sleep mode."""
        return max(0, math.ceil(self._deadline - time.monotonic()))

    def _start_waiting(self, seconds: int) -> None:
        self._stop_waiting()
        self._deadline = time.monotonic() + seconds
        self._ticker = self.set_interval(0.5, self._tick)
        self._tick()

    def _tick(self) -> None:
        if not self._wait:
            self._stop_waiting()
            self._message("You can try again now.", error=False)
        elif self._error:
            self._message(f"{self._error.message} {_duration(self._wait)} left.")

    def _stop_waiting(self) -> None:
        self._deadline = 0.0
        if self._ticker:
            self._ticker.stop()
            self._ticker = None

    # -- stages -------------------------------------------------------------

    def _connect(self) -> None:
        self._show(Stage.CONNECTING, "Connecting to Telegram...", "Ctrl+S: setup    Ctrl+Q: quit")
        self._connecting_since = time.monotonic()
        if self._progress:
            self._progress.stop()
        self._progress = self.set_interval(1, self._show_progress)
        self._run(self._do_connect(), "")

    def _show_progress(self) -> None:
        seconds = int(time.monotonic() - self._connecting_since)
        if self.stage is not Stage.CONNECTING:
            if self._progress:
                self._progress.stop()
        elif seconds >= 5:
            self._message(f"Still trying, {seconds}s. No answer yet.", error=False)

    async def _do_connect(self) -> None:
        if await self._backend.connect() is AuthStep.READY:
            self.post_message(self.SignedIn())
        else:
            self._ask_phone()

    def _ask_phone(self) -> None:
        self._show(
            Stage.PHONE,
            "Your phone number, with country code.",
            "Enter: send code    Ctrl+S: setup    Ctrl+Q: quit",
        )

    def _ask_code(self, sent: CodeSent) -> None:
        self._sent = sent
        digits = f"{sent.length}-character " if sent.length else ""
        hints = "Enter: sign in    Esc: change number"
        if sent.can_resend:
            hints += "    Ctrl+R: send another way"
        self._show(Stage.CODE, f"Telegram sent a {digits}code {sent.where}.", hints)

    async def _ask_password(self) -> None:
        try:
            hint = await self._backend.password_hint()
        except BackendError:
            hint = ""  # only a convenience
        prompt = "This account has a two-step verification password."
        if hint:
            prompt += f"\nHint: {hint}"
        self._show(Stage.PASSWORD, prompt, "Enter: sign in    Esc: start again    Ctrl+Q: quit")

    # -- actions ------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        # Clearing the field ourselves must not clear the reason why.
        if event.value and not self._wait and not self._busy:
            self._message("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self._busy or self._wait:
            return
        value = event.value
        if self.stage is Stage.PHONE:
            try:
                phone = clean_phone(value)
            except ValueError as error:
                self._message(str(error))
                return
            self._run(self._do_phone(phone), "Asking Telegram for a code...")
        elif self.stage is Stage.CODE and value.strip():
            code = re.sub(r"[\s-]", "", ascii_digits(value))
            self._run(self._do_code(code), "Checking the code...")
        elif self.stage is Stage.PASSWORD and value:
            self._run(self._do_password(value), "Checking the password...")

    async def _do_phone(self, phone: str) -> None:
        self._ask_code(await self._backend.send_code(phone))

    async def _do_code(self, code: str) -> None:
        if await self._backend.submit_code(code) is AuthStep.PASSWORD:
            await self._ask_password()
        else:
            self.post_message(self.SignedIn())

    async def _do_password(self, password: str) -> None:
        await self._backend.submit_password(password)
        self.post_message(self.SignedIn())

    async def _do_resend(self) -> None:
        self._ask_code(await self._backend.resend_code())

    def _run(self, work: Awaitable[None], text: str) -> None:
        self._error = None
        self._set_busy(True, text)
        self.run_worker(self._guard(work), group="login", exclusive=True)

    async def _guard(self, work: Awaitable[None]) -> None:
        stage = self.stage
        try:
            await work
        except BackendError as error:
            self._set_busy(False)
            self._fail(stage, error)
        else:
            self._set_busy(False)
        field = self.query_one("#field", Input)
        if field.display:
            field.focus()

    def _fail(self, stage: Stage, error: BackendError) -> None:
        self._error = error
        if error.kind is ErrorKind.API_KEYS:
            self.post_message(self.EditSetup(error.message))
            return
        if error.kind is ErrorKind.CODE_EXPIRED:
            self._ask_phone()
        elif stage is Stage.CONNECTING:
            hints = "Ctrl+R: try again    Ctrl+S: setup    Ctrl+Q: quit"
            if error.kind is ErrorKind.SESSION:
                hints = "Ctrl+D: delete the saved login and sign in again    Ctrl+Q: quit"
            self._show(Stage.FAILED, "Not connected.", hints)
        elif error.kind in (ErrorKind.CODE, ErrorKind.PASSWORD):
            self.query_one("#field", Input).value = ""
        if error.retry_after:
            self._start_waiting(error.retry_after)
        else:
            self._message(error.message)

    def action_back(self) -> None:
        if self.stage in (Stage.CODE, Stage.PASSWORD):
            self.workers.cancel_group(self, "login")
            self._stop_waiting()
            self._set_busy(False)
            self._ask_phone()

    def action_again(self) -> None:
        if self._busy or self._wait:
            return
        if self.stage is Stage.FAILED:
            self._connect()
        elif self.stage is Stage.CODE and self._sent and self._sent.can_resend:
            self._run(self._do_resend(), "Asking for another code...")

    def action_setup(self) -> None:
        if self.stage in (Stage.CONNECTING, Stage.FAILED, Stage.PHONE):
            self.workers.cancel_group(self, "login")
            self.post_message(self.EditSetup())

    def action_forget(self) -> None:
        if self.stage is Stage.FAILED and self._error and self._error.kind is ErrorKind.SESSION:
            self.post_message(self.Forget())


def _duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02}m"
    return f"{minutes}m {secs:02}s" if minutes else f"{secs}s"
