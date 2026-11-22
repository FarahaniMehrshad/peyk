"""Made-up files for --demo and tests. Pictures are painted when asked for."""

from __future__ import annotations

import io
import math
from functools import lru_cache

from PIL import Image, ImageDraw

from peyk.models import Media

_SKIES = (
    ((24, 60, 130), (250, 170, 90)),
    ((90, 160, 230), (220, 235, 250)),
    ((40, 20, 80), (230, 90, 120)),
    ((10, 80, 90), (180, 230, 190)),
)


def content(media: Media) -> bytes:
    """The whole file."""
    if media.kind == "photo":
        return picture(media)
    if media.mime == "text/plain":
        return b"Notes of the meeting\r\n" + b"- nothing much\r\n" * (media.size // 16)
    return (b"made-up file " * (media.size // 13 + 1))[: media.size]


def picture(media: Media) -> bytes:
    if media.kind == "sticker":
        return _sticker(media.id)
    return _landscape(media.id, media.width, media.height)


@lru_cache(maxsize=32)
def tiny(media: Media) -> bytes:
    """What Telegram sends along with the message: about 40 pixels, blurred."""
    image = Image.open(io.BytesIO(picture(media))).convert("RGB")
    image.thumbnail((40, 40))
    return _encoded(image, "JPEG", quality=30)


def size_of(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


def _encoded(image: Image.Image, kind: str, **options) -> bytes:
    out = io.BytesIO()
    image.save(out, kind, **options)
    return out.getvalue()


@lru_cache(maxsize=32)
def _landscape(seed: int, width: int, height: int) -> bytes:
    top, bottom = _SKIES[seed % len(_SKIES)]
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        part = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(round(a + (b - a) * part) for a, b in zip(top, bottom, strict=True)),
        )
    r = min(width, height) // 8
    x, y = width * (2 + seed % 5) // 8, height // 4
    draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 235, 170))
    for layer in range(3):
        base = height * (5 + layer) // 8
        points = [(0, height)]
        for step in range(41):
            wave = math.sin(step * 0.35 + seed + layer * 2) + math.sin(step * 0.9 + seed * 2)
            points.append((width * step // 40, base - round(wave * height / 22)))
        points.append((width, height))
        draw.polygon(points, fill=(40 - layer * 10, 90 - layer * 22, 80 - layer * 18))
    return _encoded(image, "JPEG", quality=85)


@lru_cache(maxsize=4)
def _sticker(seed: int) -> bytes:
    image = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse([20, 20, 492, 492], fill=(255, 205, 60, 255), outline=(150, 100, 0, 255), width=10)
    for x in (170, 342):
        draw.ellipse([x - 30, 160, x + 30, 240], fill=(60, 40, 0, 255))
    draw.arc([130, 180, 382, 400], 20, 160, fill=(60, 40, 0, 255), width=22)
    return _encoded(image, "WEBP")
