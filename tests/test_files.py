"""File names from strangers, saving, opening, and pictures."""

import io
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from peyk import files, images
from peyk.backend import sample_files
from peyk.backend.base import BackendError, ErrorKind
from peyk.files import Sink, Source, can_open, existing, finish, safe_name
from peyk.images import Box
from peyk.models import Media
from peyk.platform import opener
from peyk.platform.graphics import Graphics, detect
from peyk.platform.opener import CannotOpen

SENT = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
RLO = chr(0x202E)
ZWNJ = chr(0x200C)


# -- names


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("notes.pdf", "notes.pdf"),
        ("..\\..\\Startup\\x.bat", "x.bat"),
        ("C:/Windows/x.txt", "x.txt"),
        ("a:b.txt", "a_b.txt"),
        ("x.txt:stream", "x.txt_stream"),
        (f"photo{RLO}gpj.exe", "photogpj.exe"),
        ("a\x00b\x1b[31m.txt", "ab[31m.txt"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("com1.tar.gz", "_com1.tar.gz"),
        ("report.pdf. . ", "report.pdf"),
        ("evil.exe .", "evil.exe"),
        (".hidden", "hidden"),
        ("", "file"),
        ("...", "file"),
        ("???", "___"),
        (f"می{ZWNJ}خواهم.pdf", f"می{ZWNJ}خواهم.pdf"),
        ("a" * 400 + ".jpg", "a" * files.MAX_NAME + ".jpg"),
    ],
)
def test_safe_names(name, safe):
    assert safe_name(name) == safe


def test_any_name_can_be_written(tmp_path):
    rng = random.Random(4)
    pool = [chr(i) for i in range(0, 0x250)] + [RLO, ZWNJ, "می", "😀", "..", "\\", "/", "con", "."]
    folder = tmp_path / "in"
    folder.mkdir()
    for _ in range(400):
        name = safe_name("".join(rng.choices(pool, k=rng.randint(0, 12))))
        path = folder / name
        path.write_bytes(b"x")
        assert path.resolve().parent == folder.resolve() and path.is_file(), repr(name)
        assert [p.name for p in folder.iterdir()] == [name]
        path.unlink()


def test_only_harmless_types_open():
    for name in ("a.jpg", "a.PDF", "b.mp4", "c.docx"):
        assert can_open(Path(name))
    for name in ("a.exe", "a.bat", "a.jpg.exe", "a.lnk", "a.js", "a.ps1", "a.html", "a.svg",
                 "a.url", "a.docm", "a.msi", "a.scr", "a.iso", "a", "exe"):  # fmt: skip
        assert not can_open(Path(name))


# -- saving


def saved(folder, name="a.txt", data=b"hello"):
    part = folder / "x.part"
    sink = Sink(part)
    sink.write(data)
    sink.close()
    return finish(part, name, SENT)


def test_finished_downloads_never_replace_anything(tmp_path):
    folder = tmp_path / "new" / "folder"
    first, second, third = (saved(folder) for _ in range(3))
    assert [p.name for p in (first, second, third)] == ["a.txt", "a (2).txt", "a (3).txt"]
    assert not (folder / "x.part").exists()


def test_downloads_are_marked_as_from_the_web(tmp_path):
    path = saved(tmp_path)
    assert "ZoneId=3" in Path(f"{path}:Zone.Identifier").read_text()
    assert path.read_bytes() == b"hello"


def test_a_file_already_there_is_found(tmp_path):
    path = saved(tmp_path)
    assert existing(tmp_path, "a.txt", 5, SENT) == path
    assert existing(tmp_path, "a.txt", 6, SENT) is None
    assert existing(tmp_path, "a.txt", 5, SENT.replace(hour=11)) is None
    other = saved(tmp_path, data=b"other one")
    assert existing(tmp_path, "a.txt", 9, SENT) == other


def test_disk_trouble_is_not_network_trouble(tmp_path):
    (tmp_path / "file").write_text("x")
    with pytest.raises(BackendError) as found:
        Sink(tmp_path / "file" / "a.part")
    assert found.value.kind is ErrorKind.DISK
    with pytest.raises(BackendError) as found:
        Source(tmp_path / "missing")
    assert found.value.kind is ErrorKind.DISK


# -- opening


def test_opening(launched, tmp_path):
    opener.open_file(Path("a.jpg"))
    opener.open_link("https://example.com/?a=1&b=2")
    opener.show(tmp_path)
    opener.show(tmp_path / "a b.exe")
    assert launched == [
        "a.jpg",
        "https://example.com/?a=1&b=2",
        f'explorer.exe "{tmp_path}"',
        f'explorer.exe /select,"{tmp_path / "a b.exe"}"',
    ]


def test_programs_and_odd_links_are_refused(launched):
    with pytest.raises(CannotOpen):
        opener.open_file(Path("a.exe"))
    for url in ("file:///C:/Windows/System32/calc.exe", "calc.exe", "javascript:alert(1)",
                "ms-x:y", "http://", "https://a b", 'https://a"b', "\\\\host\\share",
                "https://[x"):  # fmt: skip
        with pytest.raises(CannotOpen):
            opener.open_link(url)
    assert launched == []


# -- pictures


def jpeg(width, height, **extra):
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 40)).save(out, "JPEG", **extra)
    return out.getvalue()


def test_decoding():
    assert max(images.decode(jpeg(800, 400), 100).size) == 100
    for junk in (b"", b"nonsense", jpeg(80, 80)[:100], b"%!PS-Adobe-3.0 EPSF-3.0\n"):
        images.decode(junk, 100)  # does not raise
    assert images.decode(b"nonsense", 100) is None


def test_huge_pictures_are_not_decoded():
    out = io.BytesIO()
    Image.new("1", (12_000, 12_000)).save(out, "PNG")
    assert images.decode(out.getvalue(), 100) is None


def test_fitting():
    cell = (10, 20)
    assert images.fit(1280, 960, 36, 12, cell) == Box(32, 12)
    assert images.fit(100, 100, 36, 12, cell) == Box(10, 5)  # never larger than it is
    assert images.fit(100, 100, 36, 12, cell, grow=2) == Box(20, 10)
    assert images.fit(5000, 10, 36, 12, cell) == Box(36, 1)
    assert images.fit(10, 5000, 36, 12, cell) == Box(2, 12)
    assert images.fit(0, 10, 36, 12, cell) is None
    assert images.fit(10, 10, 1, 12, cell) is None


def test_blocks_fill_their_box():
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for half in (True, False):
        rows = images.blocks(image, Box(7, 3), (1, 2, 3), half)
        assert len(rows) == 3
        assert all(sum(s.cell_length for s in row) == 7 for row in rows)
        assert rows[0][0].style.bgcolor.triplet == (1, 2, 3)  # the background shows through
    assert "▀" not in "".join(s.text for row in rows for s in row)


def test_sixels():
    data = images.sixel(Image.new("RGB", (50, 50)), Box(5, 3), (10, 20), (0, 0, 0), 0, 3)
    part = images.sixel(Image.new("RGB", (50, 50)), Box(5, 3), (10, 20), (0, 0, 0), 1, 2)
    assert data.startswith("\x1bP") and data.endswith("\x1b\\")
    assert "50;60" in data and "50;20" in part


def test_photos_lose_their_metadata(tmp_path):
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    exif[0x0112] = 6  # turned on its side
    path = tmp_path / "a.jpg"
    path.write_bytes(jpeg(4000, 1000, exif=exif))
    data = images.as_photo(path)
    assert b"SecretCamera" not in data and not Image.open(io.BytesIO(data)).getexif()
    assert Image.open(io.BytesIO(data)).size == (640, 2560)


def test_what_cannot_be_a_photo(tmp_path):
    long, junk = tmp_path / "long.jpg", tmp_path / "junk.jpg"
    long.write_bytes(jpeg(2100, 100))
    junk.write_bytes(b"junk")
    assert images.as_photo(long) is images.as_photo(junk) is None
    assert images.is_photo(long) and not images.is_photo(tmp_path / "a.gif")


def test_sample_pictures_decode():
    for kind, size in (("photo", (300, 200)), ("sticker", (512, 512))):
        media = Media(kind, "", 1, picture=True, width=size[0], height=size[1])
        assert images.decode(sample_files.picture(media), 512).size == size


def test_graphics_settings(monkeypatch):
    monkeypatch.delenv("WT_SESSION", raising=False)
    assert detect("off") == Graphics(on=False)
    assert detect("blocks") == Graphics()
    monkeypatch.setenv("WT_SESSION", "1")
    assert detect("blocks") == Graphics(half=False)
