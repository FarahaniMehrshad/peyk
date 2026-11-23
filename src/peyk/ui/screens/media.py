"""The part of the main screen that deals with files, pictures and links."""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from textual import on

from peyk import files
from peyk.backend.base import BackendError
from peyk.models import Attachment, Media, Message, State
from peyk.platform import opener
from peyk.platform.opener import CannotOpen
from peyk.ui.screens.confirm import Answer, ChoiceScreen
from peyk.ui.screens.picker import FilePicker
from peyk.ui.screens.viewer import ViewerScreen
from peyk.ui.widgets.conversation import Conversation

if TYPE_CHECKING:
    from peyk.ui.screens.main import MainScreen as _Base
else:
    from textual.screen import Screen as _Base  # a plain mixin would lose its @on handlers

log = logging.getLogger(__name__)
PATIENCE = 20  # seconds; what takes longer does not jump in front of the user
PROGRESS_PAUSE = 0.3
MAX_LINKS = 9
_EXTENSIONS = {
    "photo": ".jpg", "video": ".mp4", "gif": ".mp4", "video_note": ".mp4", "voice": ".ogg",
    "audio": ".mp3", "sticker": ".webp",
}  # fmt: skip


def file_name(message: Message) -> str:
    media = message.media
    assert media
    stamp = message.date.astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    made_up = f"{media.kind}_{stamp}{_EXTENSIONS.get(media.kind, '')}"
    return files.safe_name(media.name, made_up) if media.name else made_up


def links(message: Message) -> list[tuple[str, str]]:
    """(what is shown, where it goes) of the web links in a message."""
    found: list[tuple[str, str]] = []
    for span in message.spans:
        if span.kind != "link":
            continue
        shown = message.text[span.start : span.end]
        url = span.url or shown
        if not span.url and "@" in shown and "/" not in shown:
            continue  # an email address
        if "://" not in url:
            url = f"https://{url}"
        if opener.is_web_link(url) and (shown, url) not in found:
            found.append((shown, url))
    return found


class MediaActions(_Base):
    # -- pictures -----------------------------------------------------------

    @on(Conversation.Looked)
    def _looked(self, event: Conversation.Looked) -> None:
        self.gallery.want(event.messages)

    def _pictures_changed(self) -> None:
        if not self._gone and self.active is not None:
            self.conversation.content_changed()

    def do_preview(self) -> None:
        if self.active is None:
            self._flash("Open a chat first.")
            return
        if not self.gallery.graphics.on:
            self._flash("Pictures are turned off in config.toml.")
            return
        shown = [m for m in self.conversation.messages if m.media and _viewable(m.media)]
        selected = self.conversation.selected_message
        if selected is None and shown:
            seen = self.conversation.last_visible_id()
            selected = next((m for m in reversed(shown) if m.id <= seen), shown[-1])
        if selected is None or selected not in shown:
            locked = selected.media.locked if selected and selected.media else ""
            self._flash(locked or "There is no picture in this message.")
            return
        self.app.push_screen(
            ViewerScreen(
                self.gallery, self._options, shown, shown.index(selected), self._keys
            ),
            lambda message: self._open_media(message) if message else None,
        )

    # -- opening ------------------------------------------------------------

    def do_open_media(self) -> None:
        message = self._selected_or_complain("open")
        if message is None:
            return
        if message.state is not State.SENT:
            self._flash("That message is not sent yet.")
            return
        targets = links(message)[:MAX_LINKS]
        has_file = message.media is not None and (message.media.id or message.media.locked)
        if has_file and not targets:
            self._open_media(message)
        elif not has_file and len(targets) == 1:
            self._open_link(*targets[0])
        elif has_file or targets:
            self._ask_what_to_open(message, bool(has_file), targets)
        else:
            self._flash("There is nothing to open in this message.")

    def _ask_what_to_open(
        self, message: Message, has_file: bool, targets: list[tuple[str, str]]
    ) -> None:
        answers = []
        if has_file and message.media:
            answers.append(Answer("f", message.media.label, "file"))
        answers += [Answer(str(i), url, str(i)) for i, (_, url) in enumerate(targets, 1)]

        def answered(value: str | None) -> None:
            if value == "file":
                self._open_media(message)
            elif value:
                self._start_link(targets[int(value) - 1][1])  # they have seen where it goes

        self.app.push_screen(
            ChoiceScreen("Open what?", answers, options=self._options, stacked=True), answered
        )

    def _open_link(self, shown: str, url: str) -> None:
        if url in (shown, f"https://{shown}"):  # exactly: the end alone can be made to match
            self._start_link(url)
            return
        # The text does not say where it goes.
        self.app.push_screen(
            ChoiceScreen("Open this link?", [Answer("y", "open", "yes")], url, self._options),
            lambda value: self._start_link(url) if value else None,
        )

    def _start_link(self, url: str) -> None:
        self._outside(lambda: opener.open_link(url), "Opened in the browser.")

    def _outside(self, work, done: str) -> None:
        try:
            work()
        except CannotOpen as error:
            self._flash(str(error))
        else:
            self._flash(done)

    # -- downloading --------------------------------------------------------

    def _open_media(self, message: Message) -> None:
        media = message.media
        assert media
        if media.locked:
            self._flash(media.locked)
            return
        key = (message.chat_id, message.id)
        if key in self._downloads:
            self._downloads[key].cancel()
            self._flash("Download stopped.")
            return
        name = file_name(message)
        kept = files.existing(self.folder_for_files, name, media.size, message.date)
        if kept:
            self._show_file(kept)
            return
        work = self._download(message, media, name)
        self._downloads[key] = self.run_worker(work, group=f"download-{message.id}")
        self._flash(f"Downloading {name}. {self._keys['open_media']} again stops it.")

    async def _download(self, message: Message, media: Media, name: str) -> None:
        started = time.monotonic()
        key = (message.chat_id, message.id)
        part = self.folder_for_files / f"{name}.{os.getpid()}-{message.id}.part"
        done = False
        try:
            sink = files.Sink(part)
            try:
                await self._backend.download(
                    message.chat_id,
                    message.id,
                    sink,  # type: ignore[arg-type]
                    lambda so_far, total: self._show_progress(key, so_far, total or media.size),
                )
            finally:
                sink.close()
            path = files.finish(part, name, message.date)
            done = True
        except BackendError as error:
            self._flash(f"Not downloaded. {error.message}")
            return
        finally:
            self._downloads.pop(key, None)
            self._end_progress(key)
            if not done:
                with contextlib.suppress(OSError):
                    part.unlink(missing_ok=True)
        if self._gone:
            return
        if time.monotonic() - started < PATIENCE and self.app.app_focus:
            self._show_file(path)
        else:
            self._flash(f"Downloaded {path.name}. {self._keys['open_media']} opens it.")

    def _show_file(self, path: Path) -> None:
        if files.can_open(path):
            self._outside(lambda: opener.open_file(path), f"Opened {path.name}.")
        else:
            self._outside(
                lambda: opener.show(path), f"{path.name} is shown in its folder, not run."
            )

    def do_downloads(self) -> None:
        folder = self.folder_for_files
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._flash(f"The folder {folder} cannot be made.")
            return
        self._outside(lambda: opener.show(folder), f"Opened {folder}.")

    # -- progress -----------------------------------------------------------

    def _show_progress(self, key: tuple[int, int], done: int, total: int) -> None:
        text = f"{min(99, done * 100 // total)}%" if total else "..."
        if self.progress.get(key) == text:
            return
        self.progress[key] = text
        now = time.monotonic()
        if now - self._progress_shown >= PROGRESS_PAUSE:
            self._progress_shown = now
            self._progress_changed(key[0])

    def _end_progress(self, key: tuple[int, int]) -> None:
        if self.progress.pop(key, None) is not None:
            self._progress_changed(key[0])

    def _progress_changed(self, chat_id: int) -> None:
        if chat_id == self.active and not self._gone:
            self.conversation.content_changed()

    def _upload_progress(self, chat_id: int, message_id: int, done: int, total: int) -> None:
        self._show_progress((chat_id, message_id), done, total)

    # -- attaching ----------------------------------------------------------

    def do_attach(self) -> None:
        self.do_write()
        chat = self._chat
        if chat is None or not chat.can_write:
            return  # it said so
        if self.draft.edit_id:
            self._flash("Finish editing first.")
            return
        self.app.push_screen(FilePicker(self._options, self._picker_folder()), self._attached)

    def _picker_folder(self) -> Path:
        for folder in (self._last_folder, files.default_folder().parent, Path.home()):
            if folder and folder.is_dir():
                return folder
        return Path.cwd()

    def _attached(self, attachment: Attachment | None) -> None:
        if attachment is None or self.active is None:
            return
        self._last_folder = attachment.path.parent
        self.draft.attachment = attachment
        self._show_draft()
        self.box.focus()


def _viewable(media: Media) -> bool:
    return bool(media.picture and media.id)
