"""Turning notifications on and off inside Peyk, and keeping the choice in config.toml."""

import pytest

from driver import run
from peyk import config
from peyk.backend import sample
from peyk.config import Config, ConfigError, save_setting
from peyk.ui.screens.command import find
from peyk.ui.widgets.status import StatusBar
from test_ui_extras import notified


def notice(d):
    return d.screen.query_one(StatusBar).notice


# -- the file


def test_only_that_line_changes(tmp_path):
    path = tmp_path / "config.toml"
    before = (
        "# mine\r\n[notifications]\r\n# Windows notifications\r\nenabled = true   # on\r\n"
        "show_content = true\r\n\r\n[keys]\r\nquit = \"Q\"\r\n"
    )
    path.write_bytes(before.encode())
    save_setting(path, "notifications", "enabled", False)
    after = path.read_bytes().decode()
    assert after == before.replace("enabled = true   # on", "enabled = false   # on")
    assert not config.load(path).notifications
    save_setting(path, "notifications", "enabled", True)
    assert path.read_bytes().decode() == before


def test_a_byte_order_mark_from_notepad(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("﻿[notifications]\nenabled = true\n", encoding="utf-8")
    save_setting(path, "notifications", "enabled", False)
    assert path.read_text(encoding="utf-8") == "﻿[notifications]\nenabled = false\n"


def test_the_same_name_elsewhere_is_left_alone(tmp_path):
    path = tmp_path / "config.toml"
    before = "[images]\ninline = true\n#enabled = true\n[notifications]\n"
    path.write_text(before)
    save_setting(path, "notifications", "enabled", False)
    assert path.read_text() == before + "enabled = false\n"
    path.write_text("[privacy]\ntyping = true\n[notifications] # here\nshow_content = false\n")
    save_setting(path, "notifications", "enabled", False)
    assert path.read_text() == (
        "[privacy]\ntyping = true\n[notifications] # here\nenabled = false\nshow_content = false\n"
    )


def test_a_section_that_is_missing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[keys]\nquit = "Q"')
    save_setting(path, "notifications", "enabled", False)
    assert path.read_text() == '[keys]\nquit = "Q"\n\n[notifications]\nenabled = false\n'


def test_no_file_yet(tmp_path):
    path = tmp_path / "new" / "config.toml"
    save_setting(path, "notifications", "enabled", False)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Peyk configuration") and "enabled = false" in text
    assert config.load(path) == Config(notifications=False)


def test_a_broken_file_is_not_touched(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[notifications\nenabled = true\n")
    with pytest.raises(ConfigError):
        save_setting(path, "notifications", "enabled", False)
    assert path.read_text() == "[notifications\nenabled = true\n"
    assert not (tmp_path / "config.toml.new").exists()


def test_a_file_that_cannot_be_written(tmp_path):
    path = tmp_path / "config.toml"
    path.mkdir()  # in the way
    with pytest.raises(ConfigError, match="could not be read"):
        save_setting(path, "notifications", "enabled", False)


# -- the command bar


def test_commands_with_and_without_arguments():
    assert find("notifications off").action == "notifications"
    assert find(":notif on").action == "notifications"
    assert find("quit").choices == ()


async def test_what_cannot_follow_a_command():
    async with run() as d:
        await d.press(":", *"notifications maybe", "enter")
        assert "notifications takes on or off." in d.text()
        await d.press("ctrl+u", *"quit now", "enter")
        assert "quit takes nothing after it." in d.text()


# -- in Peyk


async def test_off_and_on_again():
    async with notified() as d:
        await d.press(":", *"notifications off", "enter")
        assert notice(d) == "Notifications off until Peyk ends."
        assert d.notes.events == ["cleared for good"] and d.notes.on_click is None
        d.backend.receive(sample.SARA, "hello")
        await d.settle()
        assert d.notes.shown == {}
        await d.press(":", *"notifications", "enter")  # alone, it switches
        assert notice(d) == "Notifications on until Peyk ends."
        d.backend.receive(sample.SARA, "again")
        await d.settle()
        assert d.notes.shown[sample.SARA][1] == "again" and d.notes.on_click is not None


async def test_turned_on_when_they_started_off():
    async with notified(config=Config(notifications=False)) as d:
        await d.press(":", *"notifications on", "enter")
        d.backend.receive(sample.SARA, "hello")
        await d.settle()
        assert sample.SARA in d.notes.shown


async def test_already():
    async with notified() as d:
        await d.press(":", *"notif on", "enter")
        assert notice(d) == "Notifications are already on." and d.notes.events == []


async def test_without_a_notifier():
    async with run() as d:  # as in tests: nothing to show them with
        await d.press(":", *"notifications off", "enter")
        assert notice(d) == "Notifications are already off."
        await d.press(":", *"notifications on", "enter")
        assert notice(d) == "Notifications cannot be shown here."


async def test_the_choice_is_kept(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[notifications]\nenabled = true\nshow_content = false\n")
    async with notified() as d:
        d.screen._config_path = path
        await d.press(":", *"notifications off", "enter")
        assert notice(d) == "Notifications off, also next time."
        assert config.load(path) == Config(notifications=False, show_content=False)
        path.write_text("[notifications\n")  # broken meanwhile
        await d.press(":", *"notifications on", "enter")
        assert notice(d).startswith("Notifications on until Peyk ends: config.toml not valid")
        assert d.screen._alerts is not None  # on all the same


async def test_the_app_knows_where_config_is(tmp_path, home):
    from peyk.ui.app import PeykApp
    from peyk.ui.screens.main import MainScreen

    app = PeykApp(Config(), home, backend=run().backend)
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        assert isinstance(app.screen, MainScreen) and app.screen._config_path == home.config
