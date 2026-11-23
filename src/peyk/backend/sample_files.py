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


VOICE_BYTES = 2000  # a second of Opus, about


def content(media: Media) -> bytes:
    """The whole file."""
    if media.kind == "voice":
        return _voice(media.id, media.size // VOICE_BYTES)
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


@lru_cache(maxsize=8)
def _voice(seed: int, seconds: int) -> bytes:
    """A little tune: notes that rise and fall, so the waveform has a shape."""
    import numpy as np

    from peyk import audio

    notes = (262, 294, 330, 392, 440, 392, 330, 294)
    rate, beat = audio.RATE, 0.25
    time = np.arange(int(beat * rate)) / rate
    fade = np.minimum(1, np.minimum(time, beat - time) * 40)
    parts = []
    for i in range(int(max(1, seconds) / beat)):
        pitch = notes[(i + seed) % len(notes)]
        loud = 0.15 + 0.25 * (1 + math.sin(i / 3)) / 2
        parts.append(loud * fade * np.sin(2 * math.pi * pitch * time))
    return audio.encode(np.concatenate(parts).astype(np.float32))


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


_COLOURS = (
    (255, 205, 60), (250, 120, 140), (110, 180, 250), (120, 210, 130), (190, 140, 240),
    (250, 160, 70), (90, 210, 210),
)  # fmt: skip
_INK = (60, 40, 20, 255)


@lru_cache(maxsize=64)
def _sticker(seed: int) -> bytes:
    """A face, a heart, a star or a cat, on a transparent ground."""
    image = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    colour = (*_COLOURS[seed % len(_COLOURS)], 255)
    edge = tuple(c * 6 // 10 for c in colour[:3]) + (255,)
    shape = (seed - 7000) // 100 if 7000 <= seed < 7300 else 0
    if shape == 1 and seed % 2:
        points = [
            (256 + math.cos(math.pi * (i / 5 - 0.5)) * (236 if i % 2 == 0 else 100),
             256 + math.sin(math.pi * (i / 5 - 0.5)) * (236 if i % 2 == 0 else 100))
            for i in range(10)
        ]  # fmt: skip
        draw.polygon(points, fill=colour, outline=edge, width=10)
        return _encoded(image, "WEBP")
    if shape == 1:
        draw.ellipse([40, 70, 270, 300], fill=colour)
        draw.ellipse([242, 70, 472, 300], fill=colour)
        draw.polygon([(52, 230), (460, 230), (256, 470)], fill=colour)
        return _encoded(image, "WEBP")
    if shape == 2:
        draw.polygon([(60, 220), (90, 30), (220, 120)], fill=colour, outline=edge, width=8)
        draw.polygon([(452, 220), (422, 30), (292, 120)], fill=colour, outline=edge, width=8)
    draw.ellipse([20, 40, 492, 500], fill=colour, outline=edge, width=10)
    mood = seed % 4
    for x in (170, 342):
        if mood == 3 and x == 342:
            draw.line([x - 35, 210, x + 35, 210], fill=_INK, width=18)  # a wink
        else:
            draw.ellipse([x - 30, 170, x + 30, 250], fill=_INK)
    if mood == 1:
        draw.ellipse([196, 320, 316, 430], fill=_INK)  # surprised
    elif mood == 2:
        draw.arc([150, 340, 362, 500], 200, 340, fill=_INK, width=22)  # sad
    else:
        draw.arc([130, 200, 382, 420], 20, 160, fill=_INK, width=22)
    if shape == 2:
        for side in (-1, 1):
            for dy in (-20, 20):
                draw.line([256 + side * 120, 330 + dy, 256 + side * 230, 320 + dy * 2],
                          fill=_INK, width=6)  # fmt: skip
    return _encoded(image, "WEBP")
