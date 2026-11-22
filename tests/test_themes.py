"""Taking over the colours of the terminal."""

from dataclasses import replace

import pytest
from PIL import Image
from textual.color import Color

from driver import run
from peyk import images
from peyk.backend import sample
from peyk.backend.fake import FakeBackend
from peyk.config import Config
from peyk.images import Box
from peyk.platform import palette as palette_module
from peyk.platform.graphics import Graphics
from peyk.platform.palette import Palette, contrast, parse, readable
from peyk.ui.app import PeykApp
from peyk.ui.themes import terminal_theme


def rgb(*values):
    return tuple(((v >> 16) & 255, (v >> 8) & 255, v & 255) for v in values)


VINTAGE = Palette((0xC0, 0xC0, 0xC0), None, rgb(
    0x000000, 0x800000, 0x008000, 0x808000, 0x000080, 0x800080, 0x008080, 0xC0C0C0,
    0x808080, 0xFF0000, 0x00FF00, 0xFFFF00, 0x0000FF, 0xFF00FF, 0x00FFFF, 0xFFFFFF,
))  # fmt: skip
CAMPBELL = Palette((0xCC, 0xCC, 0xCC), (0x0C, 0x0C, 0x0C), rgb(
    0x0C0C0C, 0xC50F1F, 0x13A10E, 0xC19C00, 0x0037DA, 0x881798, 0x3A96DD, 0xCCCCCC,
    0x767676, 0xE74856, 0x16C60C, 0xF9F1A5, 0x3B78FF, 0xB4009E, 0x61D6D6, 0xF2F2F2,
))  # fmt: skip
POWERSHELL = replace(CAMPBELL, background=(0x01, 0x24, 0x56))
LIGHT = Palette((0x58, 0x60, 0x69), (0xF6, 0xF8, 0xFA), rgb(
    0x24292E, 0xD73A49, 0x22863A, 0xB08800, 0x0366D6, 0x6F42C1, 0x1B7C83, 0x6A737D,
    0x586069, 0xCB2431, 0x28A745, 0xDBAB09, 0x2188FF, 0x8A63D2, 0x3192AA, 0x959DA5,
))  # fmt: skip
RETROWAVE = Palette((0x46, 0xBD, 0xFF), (0x07, 0x08, 0x25), rgb(
    0x181A1F, 0xFF16B0, 0x929292, 0xFCEE54, 0x46BDFF, 0xFF92DF, 0xDF81FC, 0xFFFFFF,
    0xFF16B0, 0xF85353, 0xFCEE54, 0xFFFFFF, 0x46BDFF, 0xFF92DF, 0xFF901F, 0xFFFFFF,
))  # fmt: skip
SCHEMES = [VINTAGE, CAMPBELL, POWERSHELL, LIGHT, RETROWAVE]


def reply(palette):
    def one(colour):
        return "/".join(f"{v:02x}{v:02x}" for v in colour)

    parts = [f"\x1b]10;rgb:{one(palette.foreground)}\x1b\\"]
    if palette.background:
        parts.append(f"\x1b]11;rgb:{one(palette.background)}\x1b\\")
    parts += [f"\x1b]4;{i};rgb:{one(c)}\x1b\\" for i, c in enumerate(palette.colours)]
    return "".join(parts) + "\x1b[?61;4c"


@pytest.mark.parametrize("palette", SCHEMES)
def test_the_terminals_reply_is_understood(palette):
    assert parse(reply(palette)) == palette


@pytest.mark.parametrize(
    "sequence", ["", "\x1b[?61c", "\x1b]10;rgb:zz/00/00\x1b\\", "\x1b]10;rgb:ffff/ffff\x1b\\"]
)
def test_no_or_odd_replies(sequence):
    assert parse(sequence) is None


def test_short_values_and_other_terminators():
    text = reply(VINTAGE).replace("\x1b\\", "\x07").replace("rgb:c0c0/c0c0/c0c0", "rgb:f/0/80")
    assert parse(text).foreground == (255, 0, 128)


def test_trouble_while_asking_is_not_fatal(monkeypatch):
    monkeypatch.setattr(palette_module.sys, "__stdout__", None)
    assert palette_module.query() is None


def test_light_and_dark():
    assert [p.dark for p in SCHEMES] == [True, True, True, False, True]


def values(palette):
    theme = terminal_theme(palette)
    found = {name: getattr(theme, name) for name in ("primary", "secondary", "accent", "warning",
                                                     "error", "success")}  # fmt: skip
    found |= {k: v for k, v in theme.variables.items() if k in ("text-muted", "surface", "panel")}
    return found | {f"sender-{i}": theme.variables[f"sender-{i}"] for i in range(5)}


@pytest.mark.parametrize("palette", SCHEMES)
def test_everything_is_readable(palette):
    for name, value in values(palette).items():
        if name not in ("surface", "panel"):
            assert contrast(Color.parse(value).rgb, palette.base) >= 4.5, name
    raised = Color.parse(values(palette)["surface"]).rgb
    assert contrast(raised, palette.base) > 1.05  # selected lines stand out
    assert contrast(palette.foreground, raised) > 2.5  # and can be read
    theme = terminal_theme(palette)
    badge = Color.parse(theme.variables["on-primary"]).rgb
    assert contrast(badge, Color.parse(theme.primary).rgb) > 4


def test_colours_that_are_fine_are_left_alone():
    assert readable((0, 255, 255), (0, 0, 0)) == (0, 255, 255)
    assert terminal_theme(VINTAGE).primary == "#00ffff"
    assert terminal_theme(CAMPBELL).warning == "#f9f1a5"


@pytest.mark.parametrize("palette", [None, *SCHEMES])
async def test_the_background_is_the_terminals_own(palette):
    async with run(palette=palette) as d:
        await d.open(sample.BOT)
        strips = d.app.screen._compositor.render_strips()
        painted = {
            s.style.bgcolor
            for strip in strips[:-1]  # the last one is the status bar
            for s in strip
            if s.style and s.style.bgcolor and not s.style.bgcolor.is_default
        }
        assert len(painted) <= 2  # the chosen chat and the badges
        assert sum(
            s.cell_length
            for strip in strips[:-1]
            for s in strip
            if s.style and s.style.bgcolor and not s.style.bgcolor.is_default
        ) < 200  # fmt: skip


@pytest.mark.parametrize("name", ["terminal", "catppuccin-mocha", "nord", "solarized-light"])
async def test_every_screen_works_in_every_theme(name):
    async with run(config=Config(theme=name), palette=VINTAGE) as d:
        assert d.app.theme == name
        await d.open(sample.PHOTOS)
        for key in ("f1", "slash", "colon", "p", "a"):
            await d.press(key)
            assert d.text().strip()
            await d.press("escape")
            await d.press("escape")


async def test_an_unknown_theme_falls_back():
    app = PeykApp(Config(theme="nonsense"), None, backend=FakeBackend(), palette=VINTAGE)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "terminal"


# -- transparent pictures on a background we do not know


def half_transparent():
    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (0, 0, 20, 40))
    return image


@pytest.mark.parametrize("half", [True, False])
def test_transparent_cells_are_left_to_the_terminal(half):
    rows = images.blocks(half_transparent(), Box(4, 2), None, half)
    for row in rows:
        cells = [(c, s.style) for s in row for c in s.text]
        assert len(cells) == 4
        assert cells[0][1].bgcolor and cells[0][1].bgcolor.triplet == (255, 0, 0)
        assert cells[3][1].bgcolor is None and cells[3][0] == " "


def test_transparent_sixels():
    data = images.sixel(half_transparent(), Box(4, 2), (10, 20), None, 0, 2)
    assert data.startswith("\x1bP0;1;")  # leaves what is under transparent pixels
    data = images.sixel(half_transparent(), Box(4, 2), (10, 20), (0, 0, 0), 0, 2)
    assert data.startswith("\x1bP0;0;")


async def test_stickers_with_sixels_have_no_partial_cells_under_them():
    async with run(
        size=(120, 60), palette=VINTAGE, graphics=Graphics(sixel=True, half=False)
    ) as d:
        await d.open(sample.PHOTOS)
        await d.settle(0.5)
        sticker = next(m for m in d.talk.messages if m.media and m.media.kind == "sticker")
        rows = d.screen.gallery.blocks(sticker.media, Box(12, 6), None)
        first = rows[0][0]
        assert first.style is None or first.style.bgcolor is None  # its corner
