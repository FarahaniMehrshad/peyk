"""Choose a file to send: walk through folders, filter by typing, or paste a path."""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from peyk.images import is_photo
from peyk.models import Attachment
from peyk.text import format as fmt
from peyk.text.match import ranked
from peyk.text.sanitize import sanitize_line
from peyk.ui.screens.finder import STEPS
from peyk.ui.widgets.edit_box import EditBox
from peyk.ui.widgets.results import Entry, ResultList
from peyk.ui.widgets.strips import TextOptions

MAX_LISTED = 5000


@dataclass(frozen=True, slots=True)
class Item:
    path: Path
    folder: bool
    size: int = 0
    changed: float = 0.0

    @property
    def name(self) -> str:
        return self.path.name


def listing(folder: Path) -> list[Item]:
    """Newest first, without hidden files."""
    hidden = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM
    items: list[Item] = []
    with os.scandir(folder) as found:
        for entry in found:
            if len(items) >= MAX_LISTED:
                break
            try:
                info = entry.stat()
                # A link's own attributes: the folders Windows keeps for old programs.
                own = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if own.st_file_attributes & hidden or entry.name.startswith("."):
                continue
            is_folder = entry.is_dir()
            size = 0 if is_folder else info.st_size
            items.append(Item(Path(entry.path), is_folder, size, info.st_mtime))
    items.sort(key=lambda item: -item.changed)
    return items


def typed_path(text: str) -> Path | None:
    """A path that was pasted or typed out, perhaps in quotes (as the terminal drops them)."""
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    if text.startswith("~"):
        text = os.path.expanduser(text)
    path = Path(text) if text and "\0" not in text else None
    return path if path is not None and path.is_absolute() else None


class FilePicker(ModalScreen[Attachment | None]):
    def __init__(self, options: TextOptions, folder: Path) -> None:
        super().__init__()
        self._options = options
        self.folder = folder
        self.as_photo = True
        self._items: list[Item] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="frame"), Vertical(classes="finder"):
            yield EditBox(self._options, id="query", placeholder="Type to filter, or paste a path")
            yield ResultList(self._options)
            yield Static("", markup=False, classes="finder-hints")

    @property
    def box(self) -> EditBox:
        return self.query_one("#query", EditBox)

    @property
    def results(self) -> ResultList:
        return self.query_one(ResultList)

    def on_mount(self) -> None:
        self.box.focus()
        self._go(self.folder)

    # -- folders

    def _go(self, folder: Path) -> None:
        self.run_worker(self._list(folder), group="list", exclusive=True)

    async def _list(self, folder: Path) -> None:
        """Off the event loop: the folder may be on a slow network drive."""
        came_from = self.folder
        try:
            items = await asyncio.to_thread(listing, folder)
        except OSError as error:
            self.results.set_note(f"Cannot look in there: {error.strerror or 'unknown reason'}.")
            return
        if not self.is_attached:
            return
        self.folder, self._items = folder, items
        self.box.set("")
        self._show()
        for i, entry in enumerate(self.results.entries):
            if isinstance(entry.value, Item) and entry.value.path == came_from:
                self.results.move(i)

    def _show(self) -> None:
        text = self.box.text.strip()
        path = typed_path(text)
        if path:
            self.run_worker(self._show_path(path), group="list", exclusive=True)
        else:
            found = ranked(text, self._items, lambda item: (item.name, ""))
            note = "" if found else "Nothing here." if not text else "Nothing found."
            self.results.show([self._entry(item) for item in found], note)
        self._set_title()

    async def _show_path(self, path: Path) -> None:
        self.results.show([], "Looking...")
        try:
            info = await asyncio.to_thread(path.stat)
        except (OSError, ValueError):
            info = None
        if not self.is_attached or typed_path(self.box.text) != path:
            return
        if info is None:
            self.results.show([], "There is no such file.")
            return
        item = Item(path, stat.S_ISDIR(info.st_mode), info.st_size, info.st_mtime)
        self.results.show([self._entry(item)])

    def _entry(self, item: Item) -> Entry:
        name = sanitize_line(item.name or str(item.path))
        if item.folder:
            return Entry(f"{name}\\", item, right="folder")
        when = fmt.list_date(datetime.fromtimestamp(item.changed).astimezone())
        return Entry(name, item, right=f"{fmt.size(item.size)}   {when}")

    def _set_title(self) -> None:
        path = sanitize_line(str(self.folder))
        most = max(10, min(self.app.size.width, 92) - 12)
        path = path if len(path) <= most else f"…{path[1 - most :]}"  # its end says more
        text, _ = self._options.fit(path, most)
        self.query_one(".finder").border_title = text.replace("[", r"\[")
        how = "photos" if self.as_photo else "files"
        self.query_one(".finder-hints", Static).update(
            f"Tab: pictures are sent as {how}    Backspace: folder above    Esc: close"
        )

    # -- choosing

    @on(EditBox.Changed)
    def _filter(self) -> None:
        self._show()

    @on(EditBox.Submitted)
    @on(ResultList.Chosen)
    def _choose(self) -> None:
        entry = self.results.chosen
        item = entry.value if entry else None
        if not isinstance(item, Item):
            return
        if item.folder:
            self._go(item.path)
        elif item.size == 0:
            self.results.set_note("That file is empty.")
        elif item.size > MAX_SIZE:
            self.results.set_note("Telegram takes files of up to 2 GB.")
        else:
            photo = self.as_photo and is_photo(item.path)
            self.dismiss(Attachment(item.path, item.size, photo))

    @on(EditBox.Left)
    def _close(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        step = STEPS.get(event.key)
        if step:
            self.results.move(step)
        elif event.key == "tab":
            self.as_photo = not self.as_photo
            self._set_title()
        elif event.key == "escape":
            self.dismiss(None)
        else:
            return
        event.stop()
        event.prevent_default()

    @on(EditBox.Emptied)
    def _up(self) -> None:
        if self.folder.parent != self.folder:
            self._go(self.folder.parent)
        else:
            self.run_worker(self._list_drives(), group="list", exclusive=True)

    async def _list_drives(self) -> None:
        drives = await asyncio.to_thread(os.listdrives)
        if self.is_attached:
            self._items = [Item(Path(drive), True) for drive in drives]
            self.box.set("")
            self._show()

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:  # outside the box
            self.dismiss(None)


MAX_SIZE = 4000 * 512 * 1024  # 4000 parts of half a megabyte
