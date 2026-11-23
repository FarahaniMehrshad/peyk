"""The pictures of messages: fetched when they come into view, kept in memory only."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Coroutine
from functools import partial

from PIL import Image
from rich.segment import Segment

from peyk import images
from peyk.backend.base import Backend, BackendError, ErrorKind
from peyk.images import Backdrop, Box
from peyk.models import Media, Message
from peyk.platform.graphics import Graphics

log = logging.getLogger(__name__)
SIDE = 360  # pixels of pictures between messages
LARGE_SIDE = 1600
AT_ONCE = 3
MAX_KEPT = 200
MAX_LARGE = 6
MAX_DRAWN = 300
RETRY = 30  # seconds after network trouble
STICKER_ROWS = 6

NOTHING, BLURRED, SHARP = range(3)
Fetch = Callable[[], Awaitable[bytes | None]]


class Gallery:
    def __init__(
        self,
        backend: Backend,
        graphics: Graphics,
        start: Callable[[Coroutine], object],
        changed: Callable[[], None],
    ) -> None:
        """`start` runs a coroutine in the background."""
        self._backend = backend
        self.graphics = graphics
        self._start = start
        self._changed = changed
        self._sharp: OrderedDict[int, Image.Image] = OrderedDict()
        self._blurred: OrderedDict[int, Image.Image | None] = OrderedDict()
        self._large: OrderedDict[int, Image.Image] = OrderedDict()
        self._not_before: dict[int, float] = {}
        # Pictures of stickers and GIFs go first: they are asked for by a popup on top.
        self._wanted: dict[str, list[tuple[Media, Fetch]]] = {"files": [], "messages": []}
        self._fetching: set[int] = set()
        self._workers = 0
        self._drawn: OrderedDict[tuple, object] = OrderedDict()
        self._watchers: list[Callable[[], None]] = []

    def watch(self, changed: Callable[[], None]) -> Callable[[], None]:
        """Called too when a picture arrives. Returns what stops it."""
        self._watchers.append(changed)
        return lambda: self._watchers.remove(changed)

    def _arrived(self) -> None:
        self._changed()
        for watcher in list(self._watchers):
            watcher()

    # -- what there is ------------------------------------------------------

    def box(self, media: Media, max_cols: int, max_rows: int) -> Box | None:
        """Known before the picture is there, so nothing moves when it arrives."""
        if not media.picture or not media.id:
            return None
        if media.kind == "sticker":
            max_rows = min(max_rows, STICKER_ROWS)
        cell = self.graphics.cell
        return images.fit(media.width, media.height, max_cols, max_rows, cell)

    def state(self, media: Media) -> int:
        if self.sharp(media):
            return SHARP
        return BLURRED if self._blur(media) else NOTHING

    def sharp(self, media: Media) -> Image.Image | None:
        return self._sharp.get(media.id)

    def best(self, media: Media) -> Image.Image | None:
        return self._large.get(media.id) or self._sharp.get(media.id) or self._blur(media)

    def _blur(self, media: Media) -> Image.Image | None:
        if not media.preview:
            return None
        if media.id not in self._blurred:
            _keep(self._blurred, media.id, images.decode(media.preview, 64), MAX_KEPT)
        return self._blurred[media.id]

    # -- drawing ------------------------------------------------------------

    def blocks(
        self, media: Media, box: Box, background: Backdrop, large: bool = False
    ) -> list[list[Segment]] | None:
        image = self.best(media) if large else self.sharp(media) or self._blur(media)
        if image is None:
            return None
        key = ("blocks", media.id, image.size, box, background)
        if key not in self._drawn:
            # A sharp picture on top lets blocks show through where it is transparent.
            solid = self.graphics.sixel
            drawn = images.blocks(image, box, background, self.graphics.half, solid)
            _keep(self._drawn, key, drawn, MAX_DRAWN)
        return self._drawn[key]  # type: ignore[return-value]

    async def sixel(
        self,
        media: Media,
        image: Image.Image,
        box: Box,
        background: Backdrop,
        first: int,
        last: int,
    ) -> str:
        """Encoded outside the event loop: large pictures take a while."""
        key = ("sixel", media.id, image.size, box, background, first, last)
        if key not in self._drawn:
            data = await asyncio.to_thread(
                images.sixel, image, box, self.graphics.cell, background, first, last
            )
            _keep(self._drawn, key, data, MAX_DRAWN)
        return self._drawn[key]  # type: ignore[return-value]

    # -- fetching -----------------------------------------------------------

    def want(self, messages: list[Message]) -> None:
        """These are on screen. What was wanted before no longer is."""
        backend = self._backend
        found = [
            (m.media, partial(backend.picture, m.chat_id, m.id, SIDE)) for m in messages if m.media
        ]
        self._want("messages", found)

    def want_files(self, files: list[Media]) -> None:
        """Stickers and GIFs of our own; the same as `want`, for messages."""
        self._want("files", [(f, partial(self._backend.file_picture, f.id, SIDE)) for f in files])

    def _want(self, kind: str, found: list[tuple[Media, Fetch]]) -> None:
        now = time.monotonic()
        self._wanted[kind] = [
            (media, fetch)
            for media, fetch in found
            if media.picture
            and media.id
            and media.id not in self._sharp
            and media.id not in self._fetching
            and self._not_before.get(media.id, 0) <= now
        ]
        waiting = sum(len(some) for some in self._wanted.values())
        for _ in range(min(AT_ONCE - self._workers, waiting)):
            self._workers += 1
            self._start(self._fetch_wanted())

    def try_again(self) -> None:
        """The connection is back."""
        self._not_before.clear()

    async def _fetch_wanted(self) -> None:
        try:
            while some := next((w for w in self._wanted.values() if w), None):
                await self._fetch(*some.pop(0))
        finally:
            self._workers -= 1

    async def _fetch(self, media: Media, fetch: Fetch) -> None:
        if media.id in self._sharp or media.id in self._fetching:
            return
        self._fetching.add(media.id)
        try:
            data = await fetch()
            image = await asyncio.to_thread(images.decode, data, SIDE * 2) if data else None
        except BackendError as error:
            log.info("picture not loaded: %s", error.kind.name)
            wait = error.retry_after or RETRY
            lasting = error.kind not in (ErrorKind.NETWORK, ErrorKind.FLOOD)
            self._not_before[media.id] = float("inf") if lasting else time.monotonic() + wait
            return
        finally:
            self._fetching.discard(media.id)
        if image is None:
            self._not_before[media.id] = float("inf")
            return
        _keep(self._sharp, media.id, image, MAX_KEPT)
        self._arrived()

    async def large(self, message: Message) -> Image.Image | None:
        """For the whole window. None if there is none; may raise."""
        media = message.media
        assert media
        if media.id not in self._large:
            data = await self._backend.picture(message.chat_id, message.id, LARGE_SIDE)
            image = await asyncio.to_thread(images.decode, data, LARGE_SIDE) if data else None
            if image is None:
                return None
            _keep(self._large, media.id, image, MAX_LARGE)
        return self._large[media.id]


def _keep[K, V](store: OrderedDict[K, V], key: K, value: V, most: int) -> None:
    store[key] = value
    while len(store) > most:
        store.popitem(last=False)
