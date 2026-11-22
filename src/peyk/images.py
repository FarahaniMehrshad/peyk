"""Pictures to terminal cells and Sixels. Their bytes come from other people."""

from __future__ import annotations

import io
import logging
import math
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from rich.color import Color
from rich.segment import Segment
from rich.style import Style

log = logging.getLogger(__name__)
MAX_PIXELS = 60_000_000  # larger ones are not decoded
PHOTO_SIDE = 2560
# We check the size ourselves, and a warning on stderr would land on the screen.
warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)
_FORMATS = ("JPEG", "PNG", "WEBP", "GIF", "BMP")

RGB = tuple[int, int, int]
Backdrop = RGB | None  # None: the terminal's own background, whatever is behind it


@dataclass(frozen=True, slots=True)
class Box:
    cols: int
    rows: int


def decode(data: bytes, side: int) -> Image.Image | None:
    """At most `side` pixels in both directions. None if it is not a usable picture."""
    try:
        image = Image.open(io.BytesIO(data), formats=_FORMATS)
        if image.width * image.height > MAX_PIXELS:
            return None
        image.draft("RGB", (side, side))  # lets JPEG skip most of the work
        image = _upright(image)
        image.thumbnail((side, side))
        return image
    except Exception as error:
        log.info("picture not readable: %s", type(error).__name__)
        return None


def _upright(image: Image.Image) -> Image.Image:
    transparent = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    return ImageOps.exif_transpose(image).convert("RGBA" if transparent else "RGB")


def fit(
    width: int, height: int, max_cols: int, max_rows: int, cell: tuple[int, int], grow: float = 1.0
) -> Box | None:
    """The cells a picture takes, at most `grow` times its own size."""
    if width <= 0 or height <= 0 or max_cols < 2 or max_rows < 1:
        return None
    scale = min(max_cols * cell[0] / width, max_rows * cell[1] / height, grow)
    cols = round(width * scale / cell[0])
    rows = round(height * scale / cell[1])
    return Box(max(2, min(cols, max_cols)), max(1, min(rows, max_rows)))


def on_background(image: Image.Image, background: Backdrop, cutoff: int = 127) -> Image.Image:
    if image.mode != "RGBA":
        return image
    if background is None:  # nothing to blend with: each pixel is there or it is not
        sharp = image.copy()
        sharp.putalpha(image.getchannel("A").point(lambda a: 255 if a > cutoff else 0))
        return sharp
    flat = Image.new("RGB", image.size, background)
    flat.paste(image, mask=image.getchannel("A"))
    return flat


def blocks(
    image: Image.Image, box: Box, background: Backdrop, half: bool, solid: bool = False
) -> list[list[Segment]]:
    """One list of segments per row. With `half`, two pixels per cell.

    With `solid`, cells that are partly transparent are left out.

    Without it only backgrounds are coloured: terminals may "improve" the contrast
    of text, which ruins half blocks, but they leave backgrounds alone.
    """
    tall = 2 if half else 1
    small = image.resize((box.cols, box.rows * tall))
    small = on_background(small, background, 250 if solid else 127)
    pixels = small.load()

    def colour(x: int, y: int) -> Color | None:
        red, green, blue, *alpha = pixels[x, y]
        return None if alpha == [0] else Color.from_rgb(red, green, blue)

    rows = []
    for y in range(box.rows):
        row: list[Segment] = []
        for x in range(box.cols):
            top = colour(x, y * tall)
            bottom = colour(x, y * 2 + 1) if half else top
            if top == bottom or not half:
                row.append(Segment(" ", Style(bgcolor=top)))
            elif top is None:
                row.append(Segment("▄", Style(color=bottom)))
            else:
                row.append(Segment("▀", Style(color=top, bgcolor=bottom)))
        rows.append(list(Segment.simplify(row)))
    return rows


def sixel(
    image: Image.Image,
    box: Box,
    cell: tuple[int, int],
    background: Backdrop,
    first: int,
    last: int,
) -> str:
    """Rows `first` up to (not including) `last` of the picture."""
    from textual_image._sixel import image_to_sixels

    size = (math.ceil(box.cols * cell[0]), math.ceil(box.rows * cell[1]))
    scaled = on_background(image.resize(size, Image.Resampling.LANCZOS), background)
    if scaled.mode == "RGBA":
        return image_to_sixels(scaled.crop((0, first * cell[1], size[0], last * cell[1])))
    # The palette is of the whole picture, so a part of it has the same colours.
    palette = scaled.quantize(256, Image.Quantize.MEDIANCUT)
    scaled = scaled.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)
    if (first, last) != (0, box.rows):
        scaled = scaled.crop((0, first * cell[1], size[0], last * cell[1]))
    return image_to_sixels(scaled)


def is_photo(path: Path) -> bool:
    """Whether it can be sent as a photo rather than as a file."""
    return path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def as_photo(path: Path) -> bytes | None:
    """A JPEG of moderate size without the metadata (place, camera). None if unusable."""
    try:
        with Image.open(path, formats=_FORMATS) as opened:
            if opened.width * opened.height > MAX_PIXELS:
                return None
            image = _upright(opened)
        image.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
        short, long = sorted(image.size)
        if long > short * 20:  # Telegram's limit
            return None
        clean = Image.new("RGB", image.size)  # a fresh one carries nothing along
        clean.paste(on_background(image, (255, 255, 255)))
        out = io.BytesIO()
        clean.save(out, "JPEG", quality=88, optimize=True)
        return out.getvalue()
    except Exception as error:
        log.info("photo not readable: %s", type(error).__name__)
        return None
