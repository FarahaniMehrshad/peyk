"""Setup, sign-in and sign-out, driven by key presses against the fake backend."""

import pytest
from textual.widgets import Input

from peyk import secrets as secret_store
from peyk.backend.base import BackendError, ErrorKind, Link
from peyk.backend.fake import DEMO_CODE, DEMO_PASSWORD, FakeBackend
from peyk.config import Config
from peyk.secrets import Secrets
from peyk.ui.app import PeykApp
from peyk.ui.screens.confirm import ConfirmScreen
from peyk.ui.screens.login import LoginScreen, Stage, _duration, clean_phone
from peyk.ui.screens.main import MainScreen
from peyk.ui.screens.setup import SetupScreen, validate
from peyk.ui.widgets.bidi_label import BidiLabel
from peyk.ui.widgets.status import StatusBar

HASH = "0123456789abcdef0123456789abcdef"
PHONE = "+447700900123"
NETWORK = BackendError(ErrorKind.NETWORK, "Could not reach Telegram.")


def demo(**options):
    backend = FakeBackend(signed_in=False, **options)
    return PeykApp(Config(), None, backend=backend), backend


async def settle(pilot):
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()
    await pilot.pause()


async def enter(pilot, text):
    pilot.app.screen.query_one("#field", Input).value = text
    await pilot.press("enter")
    await settle(pilot)


def message(pilot):
    return pilot.app.screen.query_one("#message", BidiLabel).text


# -- signing in


async def test_full_sign_in():
    app, backend = demo()
    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.screen.stage is Stage.PHONE
        await enter(pilot, "0044 7700-900123")
        assert app.screen.stage is Stage.CODE
        prompt = app.screen.query_one("#prompt", BidiLabel).text
        assert "5-character code to your other Telegram apps" in prompt
        await enter(pilot, "1 2345")
        assert app.screen.stage is Stage.PASSWORD
        field = app.screen.query_one("#field", Input)
        assert field.password and field.has_focus and not field.disabled
        assert "it is 'demo'" in app.screen.query_one("#prompt").text
        await enter(pilot, DEMO_PASSWORD)
        assert isinstance(app.screen, MainScreen)
        assert app.screen.chat_list.chats
        assert app.screen.query_one(StatusBar).link is Link.ONLINE
    assert ("send_code", PHONE) in backend.calls and ("submit_code", "12345") in backend.calls
    assert backend.closed


async def test_no_password_goes_straight_in():
    app, _ = demo(password="")
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, PHONE)
        await enter(pilot, DEMO_CODE)
        assert isinstance(app.screen, MainScreen)


async def test_persian_digits_are_accepted():
    app, backend = demo()
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, "+۴۴۷۷۰۰۹۰۰۱۲۳")
        await enter(pilot, "۱۲۳۴۵")
        assert app.screen.stage is Stage.PASSWORD
    assert ("send_code", PHONE) in backend.calls


async def test_wrong_code_and_password_keep_the_message():
    app, _ = demo()
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, PHONE)
        await enter(pilot, "00000")
        assert app.screen.stage is Stage.CODE and message(pilot) == "That code is not right."
        assert app.screen.query_one("#field", Input).value == ""
        await pilot.press("1")
        await pilot.pause()
        assert message(pilot) == ""
        await enter(pilot, DEMO_CODE)
        await enter(pilot, "nope")
        assert app.screen.stage is Stage.PASSWORD
        assert message(pilot) == "That password is not right."
        assert app.screen.query_one("#field", Input).has_focus


async def test_bad_phone_is_caught_before_sending():
    app, backend = demo()
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, "7700900123")
        assert "country code" in message(pilot)
    assert not [c for c in backend.calls if c[0] == "send_code"]


async def test_expired_code_returns_to_the_phone():
    app, backend = demo()
    backend.fail("submit_code", BackendError(ErrorKind.CODE_EXPIRED, "That code has expired."))
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, PHONE)
        await enter(pilot, DEMO_CODE)
        assert app.screen.stage is Stage.PHONE and message(pilot) == "That code has expired."


async def test_resend_and_back():
    app, backend = demo()
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, PHONE)
        await pilot.press("ctrl+r")
        await settle(pilot)
        assert "by SMS" in app.screen.query_one("#prompt").text
        await pilot.press("ctrl+r")  # no further way to send it
        await settle(pilot)
        assert backend.calls.count(("resend_code", "")) == 1
        await pilot.press("escape")
        await settle(pilot)
        assert app.screen.stage is Stage.PHONE
        assert app.screen.query_one("#field", Input).has_focus


async def test_escape_while_waiting_for_telegram():
    app, backend = demo()
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, PHONE)
        backend.delay = 30
        app.screen.query_one("#field", Input).value = DEMO_CODE
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.query_one("#field", Input).disabled
        await pilot.press("escape")
        await settle(pilot)
        field = app.screen.query_one("#field", Input)
        assert app.screen.stage is Stage.PHONE and not field.disabled and field.has_focus
        backend.delay = 0
        await enter(pilot, PHONE)
        assert app.screen.stage is Stage.CODE


async def test_double_enter_sends_once():
    app, backend = demo()
    async with app.run_test() as pilot:
        await settle(pilot)
        app.screen.query_one("#field", Input).value = PHONE
        await pilot.press("enter", "enter")
        await settle(pilot)
    assert backend.calls.count(("send_code", PHONE)) == 1


async def test_flood_wait_counts_down_and_blocks():
    app, backend = demo()
    backend.fail("send_code", BackendError(ErrorKind.FLOOD, "Too many attempts.", 2))
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, PHONE)
        assert "2s left" in message(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert backend.calls.count(("send_code", PHONE)) == 1
        await pilot.pause(2.4)
        assert message(pilot) == "You can try again now."
        await enter(pilot, PHONE)
        assert app.screen.stage is Stage.CODE


async def test_connection_failure_and_retry():
    app, backend = demo()
    backend.fail("connect", NETWORK)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.screen.stage is Stage.FAILED and "Could not reach" in message(pilot)
        assert not app.screen.query_one("#field", Input).display
        await pilot.press("ctrl+r")
        await settle(pilot)
        assert app.screen.stage is Stage.PHONE


async def test_slow_connection_says_so(monkeypatch):
    app, backend = demo()
    backend.delay = 60
    async with app.run_test() as pilot:
        await pilot.pause()
        assert message(pilot) == ""
        app.screen._connecting_since -= 12
        await pilot.pause(1.2)
        assert "Still trying, 1" in message(pilot)


async def test_missing_hint_does_not_block():
    app, backend = demo()
    backend.fail("password_hint", NETWORK)
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, PHONE)
        await enter(pilot, DEMO_CODE)
        assert app.screen.stage is Stage.PASSWORD


async def test_hostile_text_is_inert():
    app, _ = demo(hint="[red]x[/red]\x1b[31m")
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, PHONE)
        await enter(pilot, DEMO_CODE)
        prompt = app.screen.query_one("#prompt", BidiLabel)
        assert "[red]x[/red]" in prompt.text and "\x1b" not in prompt.text


# -- main screen


@pytest.mark.parametrize("key", ["q", "ض"])
async def test_quit_works_on_any_layout(key, monkeypatch):
    # Stand in for a Persian layout, so this passes on machines without one.
    monkeypatch.setattr("peyk.ui.keymap.to_us_key", lambda c: {"ض": "q"}.get(c, c))
    app = PeykApp(Config(), None, backend=FakeBackend())
    async with app.run_test() as pilot:
        await settle(pilot)
        assert isinstance(app.screen, MainScreen)
        await pilot.press(key)
        await pilot.pause()
        assert app._exit


async def test_status_follows_the_link():
    backend = FakeBackend()
    app = PeykApp(Config(), None, backend=backend)
    async with app.run_test() as pilot:
        await settle(pilot)
        bar = app.screen.query_one(StatusBar)
        for link in (Link.CONNECTING, Link.OFFLINE, Link.ONLINE):
            backend.set_link(link)
            await pilot.pause()
            assert bar.link is link and link.value in bar.render().plain


async def test_logout_needs_confirmation():
    backend = FakeBackend()
    app = PeykApp(Config(), None, backend=backend)
    async with app.run_test() as pilot:
        await settle(pilot)
        for refusal in ("n", "escape", "x", "enter"):
            await pilot.press("L")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press(refusal)
            await pilot.pause()
            if isinstance(app.screen, ConfirmScreen):
                await pilot.press("n")
                await pilot.pause()
        assert ("log_out", "") not in backend.calls
        backend.fail("log_out", NETWORK)
        await pilot.press("L", "y")
        await settle(pilot)
        assert isinstance(app.screen, MainScreen)
        notice = app.screen.query_one(StatusBar).notice
        assert notice == "Still logged in. Could not reach Telegram."
        await pilot.press("L", "y")
        await settle(pilot)
        assert isinstance(app.screen, LoginScreen)


# -- setup and stored secrets


def real(home, *backends):
    queue = list(backends)
    made = []

    def factory(secrets, proxy):
        made.append((secrets, proxy))
        return queue.pop(0)

    return PeykApp(Config(), home, factory=factory), made


async def fill_setup(pilot, api_id, api_hash, proxy=""):
    for name, value in (("api-id", api_id), ("api-hash", api_hash), ("proxy", proxy)):
        pilot.app.screen.query_one(f"#{name}", Input).value = value
    pilot.app.screen.query_one("#api-id", Input).focus()
    await pilot.pause()
    await pilot.press("enter", "enter", "enter")
    await settle(pilot)


async def test_first_run_to_sign_in(home):
    app, made = real(home, FakeBackend(signed_in=False))
    async with app.run_test() as pilot:
        await settle(pilot)
        assert isinstance(app.screen, SetupScreen)
        await pilot.press("escape")  # nowhere to go back to
        assert isinstance(app.screen, SetupScreen)
        assert app.screen.query_one("#api-hash", Input).password
        await fill_setup(pilot, "abc", HASH)
        assert "API ID is a number" in message(pilot)
        await fill_setup(pilot, "123", "short")
        assert "32 characters" in message(pilot)
        await fill_setup(pilot, "123", HASH, "nonsense://x")
        assert "socks5://" in message(pilot)
        await fill_setup(pilot, "۱۲۳", HASH.upper(), "socks5://127.0.0.1:1080")
        assert isinstance(app.screen, LoginScreen) and app.screen.stage is Stage.PHONE
    assert secret_store.load(home.secrets) == Secrets(123, HASH, "socks5://127.0.0.1:1080")
    assert made[0][1].describe() == "SOCKS5 127.0.0.1:1080"


async def test_returning_user_skips_setup(home):
    secret_store.save(home.secrets, Secrets(1, HASH))
    app, _ = real(home, FakeBackend())
    async with app.run_test() as pilot:
        await settle(pilot)
        assert isinstance(app.screen, MainScreen)


async def test_config_proxy_wins(home):
    secret_store.save(home.secrets, Secrets(1, HASH, "socks5://saved:1"))
    made = []
    app = PeykApp(Config(proxy="http://configured:2"), home,
                  factory=lambda s, p: made.append(p) or FakeBackend())  # fmt: skip
    async with app.run_test() as pilot:
        await settle(pilot)
    assert made[0].host == "configured"


async def test_bad_config_proxy_is_explained(home):
    secret_store.save(home.secrets, Secrets(1, HASH))
    app = PeykApp(Config(proxy="ftp://x:1"), home, factory=lambda s, p: FakeBackend())
    async with app.run_test() as pilot:
        await settle(pilot)
        assert isinstance(app.screen, SetupScreen) and "socks5://" in message(pilot)


async def test_rejected_api_keys_return_to_setup(home):
    secret_store.save(home.secrets, Secrets(1, HASH))
    first = FakeBackend(signed_in=False)
    first.fail("send_code", BackendError(ErrorKind.API_KEYS, "Telegram rejected the API ID."))
    app, _ = real(home, first, FakeBackend(signed_in=False))
    async with app.run_test() as pilot:
        await settle(pilot)
        await enter(pilot, PHONE)
        assert isinstance(app.screen, SetupScreen)
        assert message(pilot) == "Telegram rejected the API ID."
        assert app.screen.query_one("#api-id", Input).value == "1"
        assert first.closed
        await fill_setup(pilot, "2", HASH)
        assert isinstance(app.screen, LoginScreen)
    assert secret_store.load(home.secrets).api_id == 2


async def test_setup_can_be_opened_and_left_again(home):
    secret_store.save(home.secrets, Secrets(1, HASH))
    app, _ = real(home, FakeBackend(signed_in=False), FakeBackend(signed_in=False))
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("ctrl+s")
        await settle(pilot)
        assert isinstance(app.screen, SetupScreen)
        await pilot.press("escape")
        await settle(pilot)
        assert isinstance(app.screen, LoginScreen) and app.screen.stage is Stage.PHONE


async def test_unreadable_secrets_ask_again(home):
    home.secrets.parent.mkdir(parents=True)
    home.secrets.write_bytes(b"from another machine")
    app, _ = real(home, FakeBackend())
    async with app.run_test() as pilot:
        await settle(pilot)
        assert isinstance(app.screen, SetupScreen) and "cannot be read" in message(pilot)


async def test_unreadable_session_is_only_deleted_on_request(home):
    secret_store.save(home.secrets, Secrets(1, HASH))
    home.session.write_bytes(b"old")
    first = FakeBackend()
    first.fail("connect", BackendError(ErrorKind.SESSION, "The saved login cannot be read."))
    app, _ = real(home, first, FakeBackend(signed_in=False))
    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.screen.stage is Stage.FAILED and home.session.exists()
        await pilot.press("ctrl+d")
        await settle(pilot)
        assert not home.session.exists() and app.screen.stage is Stage.PHONE


async def test_network_failure_never_offers_deletion(home):
    secret_store.save(home.secrets, Secrets(1, HASH))
    home.session.write_bytes(b"keep")
    first = FakeBackend()
    first.fail("connect", NETWORK)
    app, _ = real(home, first)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("ctrl+d")
        await settle(pilot)
        assert home.session.exists() and app.screen.stage is Stage.FAILED


async def test_logout_forgets_everything(home):
    secret_store.save(home.secrets, Secrets(1, HASH))
    app, _ = real(home, FakeBackend())
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("L", "y")
        await settle(pilot)
        assert isinstance(app.screen, SetupScreen)
        assert app.screen.query_one("#api-hash", Input).value == ""
    assert not home.secrets.exists()


# -- small helpers


@pytest.mark.parametrize("text", ["", "+", "12345678", "+12", "+1234567890123456", "+44abc7700900"])
def test_bad_phones(text):
    with pytest.raises(ValueError):
        clean_phone(text)


def test_validate_and_duration():
    assert validate(" 12 ", f" {HASH.upper()} ", " ") == Secrets(12, HASH, "")
    assert [_duration(n) for n in (5, 65, 3700)] == ["5s", "1m 05s", "1h 01m"]


@pytest.mark.parametrize("size", [(80, 24), (40, 12), (160, 45)])
async def test_screens_fit(size, home):
    app, _ = real(home, FakeBackend(signed_in=False))
    async with app.run_test(size=size) as pilot:
        await settle(pilot)
        await fill_setup(pilot, "1", HASH)
        assert app.screen.query_one("#field", Input).region.width > 10


async def test_setup_shows_every_field_at_80x24(home):
    app, _ = real(home)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        for name in ("#api-id", "#api-hash", "#proxy", ".hints"):
            region = app.screen.query_one(name).region
            assert 0 < region.y < 23, name
