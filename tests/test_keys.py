"""Choosing one's own keys."""

import pytest

from driver import run
from peyk import config
from peyk.backend import sample
from peyk.config import Config, ConfigError
from peyk.keys import DEFAULT_KEYS, KeyProblem, hints, normal, pretty, resolve


@pytest.mark.parametrize(("written", "key"), [
    ("j", "j"), ("J", "J"), ("+", "+"), (" ", " "), ("space", " "), ("Space", " "),
    ("plus", "+"), ("Enter", "enter"), ("return", "enter"), ("ESC", "escape"), ("F1", "f1"),
    ("f12", "f12"), ("PgDn", "pagedown"), ("page_up", "pageup"), ("Ctrl+F", "ctrl+f"),
    ("control+f", "ctrl+f"), ("ctrl + f", "ctrl+f"), ("alt+x", "alt+x"), ("shift+j", "J"),
    ("shift+tab", "shift+tab"), ("Shift+F5", "shift+f5"), ("ctrl+shift+left", "ctrl+shift+left"),
    ("shift+ctrl+left", "ctrl+shift+left"), ("ctrl++", "ctrl++"), ("ctrl+plus", "ctrl++"),
    ("alt+-", "alt+-"), ("del", "delete"), ('"', '"'), ("\\", "\\"),
])  # fmt: skip
def test_ways_to_write_a_key(written, key):
    assert normal(written) == key
    assert normal(key) == key


@pytest.mark.parametrize("written", [
    "jj", "f13", "ctrl", "ctrl+", "hyper+x", "shift+1", "ی", "ctrl+ی", "enter enter", "+x",
    "\t", "é", "👍", "ctrl+jj",
])  # fmt: skip
def test_what_is_not_a_key(written):
    with pytest.raises(KeyProblem):
        normal(written)


def test_every_default_is_written_properly():
    assert all(normal(key) == key for key in DEFAULT_KEYS.values())
    assert resolve({}) == DEFAULT_KEYS
    assert resolve(DEFAULT_KEYS) == DEFAULT_KEYS


def test_own_keys():
    keys = resolve({"react": "-", "find_chat": "Ctrl+F", "logout": "", "fullscreen": "  "})
    assert (keys["react"], keys["find_chat"], keys["logout"]) == ("-", "ctrl+f", "")
    assert keys["fullscreen"] == "" and keys["quit"] == "q"


def test_keys_can_be_swapped_or_taken_over():
    keys = resolve({"reply": "e", "edit": "r"})
    assert (keys["reply"], keys["edit"]) == ("e", "r")
    assert resolve({"reply": "q", "quit": ""})["reply"] == "q"


@pytest.mark.parametrize(("chosen", "words"), [
    ({"reply": "e"}, ["reply", "e is already the key of edit"]),
    ({"edit": "r"}, ["edit", "r is already the key of reply"]),
    ({"reply": "z", "edit": "z"}, ["reply and edit", "same key, z"]),
    ({"replay": "z"}, ["replay", "did you mean reply?"]),
    ({"zzzzzz": "z"}, ["no action 'zzzzzz'"]),
    ({"write": "5"}, ["write: 5", "folder"]),
    ({"write": "ctrl+q"}, ["Ctrl+Q always quits"]),
    ({"write": "ctrl+m"}, ["cannot tell Ctrl+M from Enter"]),
    ({"write": "ctrl+h"}, ["Ctrl+H", "Backspace"]),
    ({"write": "ی"}, ["write", "US keyboard"]),
    ({"write": "banana"}, ["write: 'banana' is not a key"]),
    ({"write": "shift+1"}, ["character that Shift gives"]),
])  # fmt: skip
def test_problems_are_explained(chosen, words):
    with pytest.raises(KeyProblem) as problem:
        resolve(chosen)
    assert all(word in str(problem.value) for word in words), str(problem.value)
    text = "[keys]\n" + "\n".join(f'{a} = "{k}"' for a, k in chosen.items())
    with pytest.raises(ConfigError, match=r"^\[keys\] "):
        config.parse(text)


def test_keys_must_be_text():
    with pytest.raises(ConfigError, match="quit must be a string"):
        config.parse("[keys]\nquit = 1")


def test_the_template_is_all_defaults():
    assert config.parse(config.TEMPLATE) == Config()
    assert all(action in config.TEMPLATE for action in DEFAULT_KEYS)


def test_old_files_without_the_new_settings_still_work():
    parsed = config.parse("[notifications]\nenabled = true\nshow_content = true\n\n[keys]\n")
    assert parsed == Config() and parsed.show_typing
    assert not config.parse("[privacy]\ntyping = false").show_typing


@pytest.mark.parametrize(("key", "shown"), [
    ("j", "j"), ("J", "J"), (" ", "Space"), ("escape", "Esc"), ("f1", "F1"), ("tab", "Tab"),
    ("shift+tab", "Shift+Tab"), ("ctrl+f", "Ctrl+F"), ("ctrl++", "Ctrl++"), ("+", "+"),
    ("pagedown", "PgDn"), ("", ""),
])  # fmt: skip
def test_how_keys_are_shown(key, shown):
    assert pretty(key) == shown


def test_hints_leave_out_what_has_no_key():
    keys = resolve({"reply": "", "up": ""})
    parts = (("down up", "select"), ("reply", "reply"), ("help", "keys"))
    assert hints(keys, *parts) == "j select  F1 keys"


# -- in the app


def with_keys(**chosen):
    return run(config=Config(keys=resolve(chosen)))


async def test_own_keys_work_and_the_old_ones_do_not():
    async with with_keys(down="n", up="ctrl+p", write="space", fullscreen="alt+f") as d:
        first = d.screen.chat_list.selected
        await d.press("j")
        assert d.screen.chat_list.selected == first
        await d.press("n")
        assert d.screen.chat_list.selected != first
        await d.press("ctrl+p")
        assert d.screen.chat_list.selected == first
        await d.press("down", "up")  # arrows always work
        assert d.screen.chat_list.selected == first
        await d.press("enter", "alt+f")
        assert d.screen.fullscreen
        await d.press("i")
        assert not d.screen.writing
        await d.press("space")
        assert d.screen.writing


async def test_hints_show_own_keys():
    chosen = {"down": "n", "up": "ctrl+p", "write": "space", "reply": "", "open": "o",
              "open_media": "enter"}  # fmt: skip
    async with with_keys(**chosen) as d:
        assert "n/Ctrl+P move" in d.text() and "o open" in d.text()
        assert "Choose a chat and press o." in d.text()
        await d.press("o")
        assert "Space write" in d.text() and "reply" not in d.lines()[-1]
        assert "Space: write a message" in d.text()
        await d.press("d")
        assert "n/Ctrl+P select" in d.text()


async def test_help_and_popups_follow_own_keys():
    async with with_keys(down="n", up="m", help="ctrl+g", focus_chat="z", reply="") as d:
        await d.press("ctrl+g")
        assert "Ctrl+G" in d.text() and "n m" in d.text() and "config.toml" in d.text()
        page = d.app.screen.query_one("#help")
        await d.press("n", "n")
        assert page.scroll_y == 2
        await d.press("m")
        assert page.scroll_y == 1
        await d.press("ctrl+g")
        await d.open(sample.GROUP)
        await d.press("m", "+", "z")
        assert "Thumbs Up" in d.text()


async def test_unbound_actions_are_still_in_the_command_bar():
    async with with_keys(help="") as d:
        await d.press("f1")
        assert "Moving" not in d.text()
        await d.press(":", *"help", "enter")
        assert "Moving" in d.text()


# -- maps: letters that press a key, like vim's inoremap jj <Esc>


def test_maps_are_read():
    parsed = config.parse('[maps]\njj = "esc"\njk = "Enter"\n"تت" = "ctrl+w"')
    assert parsed.maps == {"jj": "escape", "jk": "enter", "تت": "ctrl+w"}
    assert Config().maps == {}


@pytest.mark.parametrize(("table", "words"), [
    ('jj = "q"', "not a key for writing"),
    ('jj = "f1"', "not a key for writing"),
    ('jj = ""', "not a key for writing"),
    ('jj = "nonsense"', "not a key"),
    ('jj = 1', "must be a string"),
    ('j = "esc"\njj = "enter"', "j is the start of jj"),
    ('"" = "esc"', "cannot be typed"),
    ('"a\tb" = "esc"', "cannot be typed"),
    (f'{"j" * 17} = "esc"', "at most 16"),
])  # fmt: skip
def test_map_problems_are_explained(table, words):
    with pytest.raises(ConfigError, match=words):
        config.parse(f"[maps]\n{table}")
