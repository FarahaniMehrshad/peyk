"""Maps made inside Peyk with :map and :unmap, and kept in config.toml."""

import pytest

from driver import run
from peyk import config
from peyk.backend import sample
from peyk.config import Config, save_map
from peyk.keys import resolve_maps
from peyk.ui.screens.main import MainScreen
from peyk.ui.widgets.status import StatusBar


def notice(d):
    return d.screen.query_one(StatusBar).notice


async def command(d, text):
    await d.press(":", *("space" if c == " " else c for c in text), "enter")


# -- the file


def test_a_map_is_added_to_a_new_file(tmp_path):
    path = tmp_path / "config.toml"
    save_map(path, "jj", "escape")
    assert config.load(path).maps == {"jj": "escape"}
    assert path.read_text(encoding="utf-8").startswith("# Peyk configuration")


def test_maps_are_added_changed_and_removed_keeping_the_rest(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(
        b'[keys]\r\nquit = "Q"\r\n\r\n[maps]\r\n"jj" = "enter"   # mine\r\nkj = "escape"\r\n'
    )
    save_map(path, "jj", "escape")
    save_map(path, "تت", "ctrl+w")
    save_map(path, "kj", None)
    assert path.read_bytes().decode() == (
        '[keys]\r\nquit = "Q"\r\n\r\n[maps]\r\n"تت" = "ctrl+w"\r\njj = "escape"   # mine\r\n'
    )
    assert config.load(path) == Config(keys=config.load(path).keys,
                                       maps={"jj": "escape", "تت": "ctrl+w"})  # fmt: skip


def test_a_section_is_made_when_missing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[privacy]\ntyping = false", encoding="utf-8")
    save_map(path, "jk", "enter")
    assert path.read_text() == '[privacy]\ntyping = false\n\n[maps]\njk = "enter"\n'


def test_a_broken_file_is_left_alone(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[maps\njj = 1\n")
    with pytest.raises(config.ConfigError):
        save_map(path, "jj", "escape")
    assert path.read_text() == "[maps\njj = 1\n"


@pytest.mark.parametrize(("written", "key"), [
    ("<Esc>", "escape"), ("<CR>", "enter"), ("<C-w>", "ctrl+w"), ("c-u", "ctrl+u"),
    ("<BS>", "backspace"), ("ESC", "escape"),
])  # fmt: skip
def test_keys_can_be_written_as_in_vim(written, key):
    assert resolve_maps({"jj": written}) == {"jj": key}


# -- the commands


async def test_map_and_unmap_inside_peyk(tmp_path):
    path = tmp_path / "config.toml"
    async with run() as d:
        d.screen._config_path = path
        await command(d, "map")
        assert "No maps" in notice(d)
        await command(d, "map jj <Esc>")
        assert notice(d) == "jj presses Esc, also next time."
        assert config.load(path).maps == {"jj": "escape"}

        await d.open(sample.SARA)
        await d.press("i", "o", "k", "j", "j")
        assert d.screen.box.text == "ok" and not d.screen.box.has_focus

        # The command bar takes jj as letters, or this could not be typed.
        await command(d, "map jk enter")
        await command(d, "map")
        assert notice(d) == "Maps: jj Esc, jk Enter."
        await command(d, "map jj")
        assert notice(d) == "jj presses Esc."

        await command(d, "unmap jj")
        assert notice(d) == "jj are letters again, also next time."
        assert config.load(path).maps == {"jk": "enter"}
        await d.press("i", "j", "j")
        assert d.screen.box.text == "okjj" and d.screen.box.has_focus


@pytest.mark.parametrize(("typed", "said"), [
    ("map jj q", "Not mapped: jj: 'q' is not a key for writing"),
    ("map j esc", "Not mapped: j is the start of jj"),
    ("map jj esc x", "map takes the letters, then a key"),
    ("unmap", "unmap takes the letters"),
    ("unmap kk", "kk is not mapped."),
])  # fmt: skip
async def test_map_problems_are_said(typed, said):
    async with run(config=Config(maps={"jj": "escape"})) as d:
        await command(d, typed)
        assert said in notice(d) and isinstance(d.app.screen, MainScreen)
        assert d.screen._options.maps == {"jj": "escape"}


async def test_without_a_file_maps_last_until_peyk_ends():
    async with run() as d:
        await command(d, "map jj esc")
        assert notice(d) == "jj presses Esc until Peyk ends."
