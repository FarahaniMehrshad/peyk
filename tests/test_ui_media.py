"""Pictures, downloads, links and attachments, driven by keys."""

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image
from textual import events

from driver import OFFLINE, run
from peyk.backend import sample
from peyk.backend.base import BackendError, ErrorKind, Link
from peyk.backend.fake import FakeBackend
from peyk.config import Config
from peyk.models import Message, Span, State
from peyk.platform.graphics import Graphics
from peyk.ui.gallery import BLURRED, SHARP
from peyk.ui.screens import media as media_module
from peyk.ui.screens import picker as picker_module
from peyk.ui.screens.confirm import ChoiceScreen
from peyk.ui.screens.main import MainScreen
from peyk.ui.screens.media import links
from peyk.ui.screens.picker import FilePicker, listing, typed_path
from peyk.ui.screens.viewer import ViewerScreen
from peyk.ui.widgets import sixels
from peyk.ui.widgets.status import StatusBar

TALL = (120, 60)
SIXEL = Graphics(sixel=True, cell=(10, 20), half=False)
SUNSET, MOUNTAIN, SPOILER, STICKER, VIDEO, SETUP, LOCKED = 21, 22, 23, 24, 25, 26, 27


def notice(d):
    return d.screen.query_one(StatusBar).notice


def calls(d, name):
    return [argument for found, argument in d.backend.calls if found == name]


def by_media(d, media_id):
    return next(m for m in d.talk.messages if m.media and m.media.id == media_id)


def by_text(d, start):
    return next(m for m in d.talk.messages if m.text.startswith(start))


async def pick(d, message):
    d.talk._select(message.id)
    d.talk._reveal(message.id)
    await d.settle()


async def look(d):
    """Long enough for pictures in view to be fetched and drawn."""
    for _ in range(2):
        await d.settle(0.25)


# -- pictures between messages


async def test_pictures_load_when_they_are_seen():
    async with run(size=TALL) as d:
        await d.open(sample.PHOTOS)
        await look(d)
        seen = [place for place, _ in d.talk._places_in_view()]
        assert len(seen) > 2
        assert all(d.screen.gallery.state(p.media) == SHARP for p in seen if not p.hidden)
        count = len(calls(d, "picture"))
        await look(d)
        assert len(calls(d, "picture")) == count  # once each


async def test_nothing_moves_when_pictures_arrive():
    async with run(size=TALL) as d:
        await d.open(sample.PHOTOS)
        before = [(b.id, b.start, len(b.lines)) for b in d.talk._blocks]
        await look(d)
        assert [(b.id, b.start, len(b.lines)) for b in d.talk._blocks] == before


async def test_spoilers_stay_hidden_until_selected():
    async with run(size=TALL) as d:
        await d.open(sample.PHOTOS)
        hidden = by_media(d, SPOILER)
        d.talk._reveal(hidden.id)
        await look(d)
        assert f"{sample.PHOTOS}:{hidden.id}:360" not in calls(d, "picture")
        assert "▒▒▒" in d.text()
        await pick(d, hidden)
        await look(d)
        assert d.screen.gallery.state(hidden.media) == SHARP
        assert "▒▒▒" not in d.text()


async def test_pictures_can_be_turned_off():
    async with run(size=TALL, graphics=Graphics(on=False)) as d:
        await d.open(sample.PHOTOS)
        await look(d)
        assert not calls(d, "picture")
        assert "▀" not in d.text() and "[Photo 800x600]" in d.text()
        await d.press("p")
        assert "turned off" in notice(d)


async def test_pictures_only_on_request():
    async with run(size=TALL, config=Config(inline_images=False)) as d:
        await d.open(sample.PHOTOS)
        await look(d)
        assert not calls(d, "picture") and "▀" not in d.text()
        await d.press("p")
        assert isinstance(d.app.screen, ViewerScreen) and "▀" in d.text()


async def test_a_failed_picture_is_tried_again_when_the_link_returns():
    async with run(size=TALL) as d:
        d.backend.fail("picture", *[OFFLINE] * 20)
        await d.open(sample.PHOTOS)
        await look(d)
        photo = by_media(d, MOUNTAIN).media
        d.talk._reveal(by_media(d, MOUNTAIN).id)
        await look(d)
        assert d.screen.gallery.state(photo) == BLURRED
        tried = len(calls(d, "picture"))
        await look(d)
        assert len(calls(d, "picture")) == tried  # not over and over
        d.backend.failures.clear()
        d.backend.set_link(Link.CONNECTING)
        d.backend.set_link(Link.ONLINE)
        await look(d)
        assert d.screen.gallery.state(photo) == SHARP


@pytest.mark.parametrize("size", [(120, 36), (80, 24), (50, 12), (30, 8)])
async def test_every_line_has_the_right_width(size):
    async with run(size=size) as d:
        await d.open(sample.PHOTOS)
        await look(d)
        for _ in range(12):
            await d.press("k")
            for line in range(d.talk.size.height):
                assert d.talk.render_line(line).cell_length == d.talk.size.width


# -- sharp ones (Sixel)


@pytest.fixture
def written(monkeypatch):
    out = []
    monkeypatch.setattr(sixels, "write", lambda widget, data: out.append(data))
    return out


async def test_sixels_are_written_where_the_pictures_are(written):
    async with run(size=TALL, graphics=SIXEL) as d:
        await d.open(sample.PHOTOS)
        await look(d)
        spots = d.talk._spots()
        assert spots and written
        for spot in spots:
            assert f"\x1b[{spot.y + 1};{spot.x + 1}H\x1bP" in written[-1]
            line = d.lines()[spot.y - 1]
            assert "[" in line  # the label is right above
        assert written[-1].startswith("\x1b7") and written[-1].endswith("\x1b8")
        assert "▀" not in d.text()


async def test_no_sixels_over_a_popup(written):
    async with run(size=TALL, graphics=SIXEL) as d:
        await d.open(sample.PHOTOS)
        await look(d)
        main = d.screen
        await d.press("f1")
        written.clear()
        main.conversation.content_changed()
        await look(d)
        assert not written
        await d.press("escape")
        await look(d)
        assert written


async def test_sixels_stay_inside_the_conversation(written):
    async with run(size=(100, 20), graphics=SIXEL) as d:
        await d.open(sample.PHOTOS)
        found = 0
        for _ in range(12):
            await d.press("k")
            await look(d)
            region = d.talk.region
            for spot in d.talk._spots():
                found += 1
                _, _, box, first, last, _ = spot.key
                assert region.y <= spot.y and spot.y + last - first <= region.bottom
                assert spot.x + box.cols <= region.right
        assert found


# -- the viewer


async def test_viewer_walks_through_the_pictures():
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SUNSET))
        await d.press("p")
        viewer = d.app.screen
        assert isinstance(viewer, ViewerScreen) and viewer.message.media.id == SUNSET
        assert "1/6" in d.text() and "▀" in d.text()
        await d.press("l")
        assert viewer.message.media.id == MOUNTAIN
        await d.press("h", "h")
        assert viewer.message.media.id == SUNSET
        await d.press("escape")
        assert isinstance(d.app.screen, MainScreen)


async def test_viewer_without_a_selection_or_a_picture():
    async with run() as d:
        await d.press("p")
        assert "Open a chat first" in notice(d)
        await d.open(sample.PHOTOS)
        await d.press("p")
        assert isinstance(d.app.screen, ViewerScreen)
        await d.press("q")
        await d.open(sample.BOT)
        await d.press("p")
        assert "no picture" in notice(d)


async def test_viewer_opens_the_file(launched, downloads):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SUNSET))
        await d.press("p", "o")
        assert isinstance(d.app.screen, MainScreen)
        assert len(launched) == 1 and launched[0].endswith(".jpg")


async def test_viewer_shows_trouble():
    async with run() as d:
        await d.open(sample.PHOTOS)
        await look(d)
        await pick(d, by_media(d, SUNSET))
        await look(d)
        d.backend.fail("picture", OFFLINE)
        await d.press("p")
        assert "Could not reach Telegram" in d.text()


async def test_viewer_in_a_tiny_window():
    async with run(size=(24, 7)) as d:
        await d.open(sample.PHOTOS)
        await d.press("p", "h", "h", "h", "h")
        await d.pilot.resize_terminal(60, 20)
        await d.settle()
        assert isinstance(d.app.screen, ViewerScreen)


async def test_viewer_draws_sixels(written):
    async with run(graphics=SIXEL) as d:
        await d.open(sample.PHOTOS)
        await d.press("p")
        written.clear()
        await d.press("h")
        await look(d)
        assert written and "\x1bP" in written[-1]


# -- downloading


async def test_open_downloads_and_opens(downloads, launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SUNSET))
        await d.press("o")
        (path,) = downloads.iterdir()
        assert path.name.startswith("photo_") and path.suffix == ".jpg"
        assert launched == [str(path)] and not d.screen.progress


async def test_a_file_is_downloaded_once(downloads, launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SETUP))
        await d.press("o", "o")
        await d.press("o")
        assert [p.name for p in downloads.iterdir()] == ["setup.exe"]
        assert len(calls(d, "download")) == 1


async def test_programs_are_shown_not_run(downloads, launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SETUP))
        await d.press("o")
        path = downloads / "setup.exe"
        assert launched == [f'explorer.exe /select,"{path}"']
        assert "not run" in notice(d)


async def test_stopping_a_download(downloads, launched):
    async with run(size=TALL) as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SETUP))
        d.backend.delay = 0.3
        await d.press_now("o")
        await d.pilot.pause(1.2)
        assert "%" in "\n".join(d.said())
        await d.press_now("o")
        assert "stopped" in notice(d)
        await d.settle()
        assert list(downloads.iterdir()) == [] and not launched and not d.screen.progress


async def test_failed_download(downloads, launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SETUP))
        d.backend.fail("download", OFFLINE)
        await d.press("o")
        assert "Not downloaded" in notice(d) and not launched
        assert list(downloads.iterdir()) == []


async def test_a_folder_that_cannot_be_made(tmp_path, launched):
    (tmp_path / "file").write_text("x")
    async with run(config=Config(folder=str(tmp_path / "file" / "sub"))) as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SETUP))
        await d.press("o")
        assert "cannot be saved" in notice(d) and not launched


async def test_no_surprise_when_it_takes_long(downloads, launched, monkeypatch):
    monkeypatch.setattr(media_module, "PATIENCE", -1)
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SETUP))
        await d.press("o")
        assert not launched and "Downloaded setup.exe" in notice(d)


async def test_locked_media_is_not_saved(downloads, launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, LOCKED))
        await d.press("o")
        assert "does not allow saving" in notice(d)
        assert not downloads.exists() and not launched


async def test_nothing_to_open():
    async with run() as d:
        await d.open(sample.PHOTOS)
        await d.press("o")
        assert "select" in notice(d).lower()
        await pick(d, by_text(d, "Pictures from"))
        await d.press("o")
        assert "nothing to open" in notice(d)


async def test_quitting_during_a_download_asks():
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SETUP))
        d.backend.delay = 0.3
        await d.press_now("o")
        await d.press_now("q")
        assert isinstance(d.app.screen, ChoiceScreen)
        assert "a download" in d.text()
        await d.press("n")


async def test_downloads_command(downloads, launched):
    async with run() as d:
        await d.press(":", *"downloads", "enter")
        assert launched == [f'explorer.exe "{downloads}"']


# -- links


def test_which_links_count():
    message = Message(
        1, 5, datetime(2026, 1, 1, tzinfo=UTC), "see example.com or me@example.com or tg",
        spans=(
            Span(4, 15, "link"),
            Span(19, 33, "link"),
            Span(37, 39, "link", "tg://resolve?domain=x"),
            Span(0, 3, "link", "file:///C:/x.exe"),
            Span(0, 3, "bold"),
        ),
    )  # fmt: skip
    assert links(message) == [("example.com", "https://example.com")]


async def test_several_links_are_offered(launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_text(d, "Two links"))
        await d.press("o")
        assert isinstance(d.app.screen, ChoiceScreen)
        assert "1: https://example.com/a" in d.text()
        assert "2: https://example.org/docs" in d.text()
        await d.press("2")
        assert launched == ["https://example.org/docs"]
        await d.press("o", "escape")
        assert len(launched) == 1


async def only(d, text, *spans):
    """Receive a message and open it."""
    d.backend.receive(sample.PHOTOS, text, spans=spans)
    await d.settle()
    await pick(d, d.talk.messages[-1])
    await d.press("o")


async def test_a_plain_link_opens_directly(launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await only(d, "https://example.com/x", Span(0, 21, "link"))
        assert launched == ["https://example.com/x"]


@pytest.mark.parametrize(
    "url", ["https://evil.example/x", "https://evil.example/?https://bank.com", "http://bank.com"]
)
async def test_a_disguised_link_is_shown_first(launched, url):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await only(d, "https://bank.com", Span(0, 16, "link", url))
        assert isinstance(d.app.screen, ChoiceScreen) and url in d.text() and not launched
        await d.press("n")
        assert not launched
        await d.press("o", "y")
        assert launched == [url]


async def test_dangerous_links_do_nothing(launched):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await only(d, "click", Span(0, 5, "link", "file:///C:/Windows/System32/calc.exe"))
        assert not launched and "nothing to open" in notice(d)


# -- choosing a file


@pytest.fixture
def folder(downloads):
    root = downloads.parent
    (root / "sub").mkdir()
    (root / "sub" / "inner.txt").write_text("inner")
    (root / "empty.txt").write_text("")
    (root / "notes.txt").write_text("notes")
    Image.new("RGB", (300, 200), "red").save(root / "pic.png")
    return root


async def test_picking_a_file(folder):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await d.press("a")
        assert isinstance(d.app.screen, FilePicker)
        assert "notes.txt" in d.text() and "sub\\" in d.text()
        await d.press(*"notes", "enter")
        assert isinstance(d.app.screen, MainScreen)
        assert "File notes.txt (5 B)" in d.text()
        await d.press(*"hello", "enter")
        sent = d.talk.messages[-1]
        assert sent.text == "hello" and sent.media.name == "notes.txt"
        assert sent.state is State.SENT and calls(d, "upload") == ["notes.txt"]
        assert not d.screen.draft.unsent


async def test_photo_or_file(folder):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await d.press("a", *"pic", "enter")
        await d.press("enter", "escape")
        assert calls(d, "upload") == ["pic.png:photo"]
        assert d.talk.messages[-1].media.kind == "photo" and not d.talk.messages[-1].text
        await d.press("a", "tab", *"pic", "enter")
        await d.press("enter")
        assert calls(d, "upload")[-1] == "pic.png"


async def test_walking_through_folders(folder):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await d.press("a", *"sub", "enter")
        picker = d.app.screen
        assert picker.folder == folder / "sub" and "inner.txt" in d.text()
        d.app.screen.box.post_message(events.Key("backspace", ""))  # as terminals send it
        await d.settle()
        assert picker.folder == folder
        assert picker.results.chosen.value.name == "sub"  # where we came from
        await d.press("escape")
        assert not d.screen.draft.unsent


async def test_unusable_files(folder, monkeypatch):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await d.press("a", *"empty", "enter")
        assert "file is empty" in d.text() and isinstance(d.app.screen, FilePicker)
        monkeypatch.setattr(picker_module, "MAX_SIZE", 2)
        d.app.screen.box.set("notes")
        d.app.screen._show()
        await d.press("enter")
        assert "2 GB" in d.text()


async def paste(d, text):
    d.app.screen.box.post_message(events.Paste(text))
    await d.settle()


async def test_a_pasted_path(folder):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await d.press("a")
        path = folder / "sub" / "inner.txt"
        await paste(d, f'"{path}"')
        await d.press("enter")
        assert d.screen.draft.attachment.name == "inner.txt"


async def test_paths_that_do_not_exist(folder):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await d.press("a")
        await paste(d, r"C:\no\such\file.txt")
        assert "no such file" in d.text()
        await d.press("enter")
        assert isinstance(d.app.screen, FilePicker)


def test_typed_paths():
    assert typed_path(r'"C:\a b.txt"') == Path(r"C:\a b.txt")
    assert typed_path("notes") is typed_path("") is typed_path("a\0b") is None
    assert typed_path("~/x").is_absolute()


def test_listing_is_newest_first(tmp_path):
    folder = tmp_path / "list"
    folder.mkdir()
    for i, name in enumerate(("old.txt", "new.txt", ".dot")):
        (folder / name).write_text("x")
        os.utime(folder / name, (1000 + i, 1000 + i))
    assert [i.name for i in listing(folder)] == ["new.txt", "old.txt"]


# -- the draft with a file


async def attach(d, name="notes"):
    await d.press("a", *name, "enter")
    assert d.screen.draft.attachment


async def test_escape_drops_the_file_before_the_reply(folder):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await pick(d, by_media(d, SUNSET))
        await d.press("r", "escape")
        await attach(d)
        assert "File notes.txt" in d.text() and "Reply" in d.text()
        await d.press("escape", "escape")
        assert d.screen.draft.attachment is None and d.screen.draft.reply


async def test_captions_are_shorter(folder):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await attach(d)
        await paste(d, "x" * 1025)
        assert "1025/1024" in d.text()
        await d.press("enter")
        assert "Too long by 1" in notice(d) and not calls(d, "upload")


async def test_files_stay_with_their_chat(folder):
    async with run() as d:
        await d.open(sample.PHOTOS)
        await attach(d)
        assert d.screen.draft.unsent
        await d.open(sample.BOT)
        assert d.screen.draft.attachment is None and "notes.txt" not in d.text()
        await d.open(sample.PHOTOS)
        assert d.screen.draft.attachment


async def test_discarding_stops_the_upload(folder):
    async with run(size=TALL) as d:
        await d.open(sample.PHOTOS)
        await attach(d)
        d.backend.delay = 0.3
        await d.press_now("enter")
        await d.pilot.pause(1.2)
        assert "%" in "\n".join(d.said())
        d.screen.outbox.discard(sample.PHOTOS, d.talk.messages[-1].id)
        await d.settle()
        assert not calls(d, "send") and not d.screen.progress
        assert d.screen.outbox.unsent == 0


async def test_d_stops_an_upload(folder):
    async with run(size=TALL) as d:
        await d.open(sample.PHOTOS)
        await attach(d)
        d.backend.delay = 0.4
        await d.press_now("enter")
        await d.press_now("escape")
        await d.press_now("k")
        await d.press_now("d")
        if isinstance(d.app.screen, ChoiceScreen):
            await d.press_now("y")
        await d.settle()
        assert d.screen.outbox.unsent == 0 and not calls(d, "send")


async def test_progress_does_not_repaint_pictures(folder, monkeypatch):
    async with run(size=TALL, graphics=SIXEL) as d:
        await d.open(sample.PHOTOS)
        await look(d)
        painted = []
        real = d.talk.refresh

        def refresh(*regions, **options):
            painted.append(regions)
            return real(*regions, **options)

        monkeypatch.setattr(d.talk, "refresh", refresh)
        setup = by_media(d, SETUP)
        d.screen.progress[sample.PHOTOS, setup.id] = "10%"
        d.screen._progress_changed(sample.PHOTOS)
        d.screen._progress_changed(sample.PHOTOS)
        ((region,),) = painted
        assert region.height == 1 and "setup.exe" in d.talk.render_line(region.y).text


async def test_text_does_not_wait_for_an_upload(folder):
    async with run(size=TALL) as d:
        await d.open(sample.PHOTOS)
        await attach(d)
        d.backend.delay = 0.2
        await d.press_now("enter")
        await d.press_now(*"quick", "enter")
        await d.settle()
        assert [c.split(":")[1] for c in calls(d, "send")] == ["quick", ""]


async def test_failed_upload_can_be_taken_back(folder):
    async with run(size=TALL) as d:
        await d.open(sample.PHOTOS)
        await attach(d)
        d.backend.fail("upload", BackendError(ErrorKind.REFUSED, "No files here."))
        await d.press(*"cap", "enter")
        await d.press("escape")
        failed = d.talk.messages[-1]
        assert failed.state is State.FAILED and "No files here." in d.text()
        await pick(d, failed)
        await d.press("e")
        assert d.screen.draft.attachment.name == "notes.txt" and d.screen.draft.text == "cap"
        await d.press("enter")
        assert d.talk.messages[-1].media.name == "notes.txt"


async def test_a_lost_connection_does_not_upload_again(folder):
    async with run(size=TALL) as d:
        await d.open(sample.PHOTOS)
        await attach(d)
        d.backend.fail("send", OFFLINE)
        await d.press("enter")
        assert d.talk.messages[-1].state is State.WAITING
        d.backend.set_link(Link.CONNECTING)
        d.backend.set_link(Link.ONLINE)
        await d.settle()
        assert calls(d, "upload") == ["notes.txt"]
        assert d.talk.messages[-1].state is State.SENT


async def test_attach_needs_a_place_to_write(folder):
    backend = FakeBackend()
    backend.chat_map[sample.NEWS] = replace(backend.chat_map[sample.NEWS], can_write=False)
    async with run(backend=backend) as d:
        await d.open(sample.NEWS)
        await d.press("a")
        assert isinstance(d.app.screen, MainScreen) and "cannot write" in notice(d)


async def test_a_sent_photo_is_shown(folder):
    async with run(size=TALL) as d:
        await d.open(sample.PHOTOS)
        await attach(d, "pic")
        await d.press("enter")
        await look(d)
        assert d.screen.gallery.state(d.talk.messages[-1].media) == SHARP
