"""Clicking a link in a message opens it."""

import pytest

from driver import run
from peyk.backend import sample
from peyk.models import Span
from peyk.ui.screens.confirm import ChoiceScreen
from peyk.ui.widgets.conversation import Conversation
from peyk.ui.widgets.status import StatusBar


def notice(d):
    return d.screen.query_one(StatusBar).notice


def where(d, text, nth=0):
    """The place of `text` in the conversation, as (column, row)."""
    found = [(line.index(text), y) for y, line in enumerate(d.said()) if text in line]
    assert found, text
    return found[nth]


async def click(d, text, offset=1, nth=0):
    x, y = where(d, text, nth)
    await d.pilot.click(Conversation, offset=(x + offset, y))
    await d.settle()


async def receive(d, text, *spans):
    d.backend.receive(sample.PHOTOS, text, spans=spans)
    await d.settle()


async def test_a_plain_link(launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await click(d, "https://example.com/a", offset=5)
        assert launched == ["https://example.com/a"]
        assert d.talk.selected == d.talk.messages[-1].id  # and the message is selected


async def test_the_text_around_it_only_selects(launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await click(d, "Two links", offset=0)
        await click(d, " and ", offset=2)
        assert launched == [] and d.talk.selected == d.talk.messages[-1].id


async def test_a_link_that_hides_where_it_goes_is_shown_first(launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await click(d, "the docs", offset=4)
        assert isinstance(d.app.screen, ChoiceScreen) and "https://example.org/docs" in d.text()
        assert launched == []
        await d.press("y")
        assert launched == ["https://example.org/docs"]


async def test_a_link_in_right_to_left_text(launched):
    async with run() as d:
        await d.open(sample.SARA)
        await click(d, "https://example.com/books", offset=8)
        assert launched == ["https://example.com/books"]


async def test_each_link_its_own(launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        text = "a.example b.example"
        await receive(d, text, Span(0, 9, "link"), Span(10, 19, "link"))
        await click(d, "b.example", offset=0)
        await click(d, "a.example", offset=8)
        assert launched == ["https://b.example", "https://a.example"]


async def test_a_link_over_two_lines(launched):
    async with run(size=(70, 30)) as d:
        await d.open(sample.PHOTOS)
        url = "https://example.com/" + "long" * 12
        await receive(d, url, Span(0, len(url), "link"))
        lines = [line for line in d.said() if "long" in line]
        assert len(lines) == 3
        await click(d, "longlong", offset=2, nth=1)
        await click(d, "longlong", offset=2, nth=2)
        assert launched == [url, url]


@pytest.mark.parametrize(("text", "span"), [
    ("mail me@example.com", Span(5, 19, "link")),
    ("run it", Span(0, 6, "link", "file:///C:/Windows/System32/calc.exe")),
])  # fmt: skip
async def test_what_is_not_a_web_link(launched, text, span):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await receive(d, text, span)
        await click(d, text[span.start : span.end], offset=1)
        assert launched == [] and notice(d) == "Only web links can be opened."


async def test_a_link_behind_a_spoiler_waits(launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        text = "secret: https://example.com/s"
        await receive(d, text, Span(8, 29, "link"), Span(8, 29, "spoiler"))
        x, y = where(d, "secret: ")
        await d.pilot.click(Conversation, offset=(x + 12, y))
        await d.settle()
        assert launched == []  # it was hidden; now that it shows, it can be clicked
        await click(d, "https://example.com/s", offset=3)
        assert launched == ["https://example.com/s"]


async def test_mentions_are_not_links(launched):
    async with run() as d:
        await d.open(sample.SARA)
        await click(d, "mina", offset=1)
        assert launched == [] and notice(d) == ""
