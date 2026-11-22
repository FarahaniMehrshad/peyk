import pytest
from bidi.algorithm import get_display

from peyk.text.bidi import ZWJ, ZWNJ, direction, layout, shape

SAMPLES = [
    "سلام دنیا",
    "می\u200cخواهم بروم",
    "قیمت 123 تومان (USD)",
    "hello سلام 2026 world",
    "مرحبا بالعالم",
    "שלום עולם",
    "سَلامٌ عَلَيكُم",
    "خانواده 👨\u200d👩\u200d👧 و 🇮🇷 و 👍🏽 و ❤️",
    "@user سلام https://example.com/a?b=1",
    "plain English text",
    "۱۲۳۴ و 12:30 و 50%",
    "a\u202eb\u202cc \u2067سلام\u2069 d",
    "\u0301leading mark",
    "لا الان سلام کلاه لَا",
    "ه\u200dب",
    "\u200f\u200c",
    "",
    " ",
    "سلام\n\nhello\nخداحافظ",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_shaping_is_one_to_one(text):
    assert len(shape(text)) == len(text)


def test_shaping_forms():
    assert shape("سلام") == "\ufeb3\ufee0\ufe8e\ufee1"
    assert shape("گچپژ") == "\ufb94\ufb7d\ufb59\ufb8b"


def test_zwnj_breaks_joining():
    assert shape("می" + ZWNJ + "خ")[1] == "\ufbfd"  # final yeh, not medial
    assert shape("میخ")[1] == "\ufbff"


def test_marks_do_not_break_joining():
    assert shape("سَل")[0] == "\ufeb3"


def test_zwj_survives_in_emoji():
    assert shape("👨\u200d👩\u200d👧") == "👨\u200d👩\u200d👧"


@pytest.mark.parametrize(
    "text", ["سلام دنیا", "קיצור 12 abc", "قیمت 123 (USD) است", "abc سلام 12 def"]
)
def test_order_matches_reference_implementation(text):
    (line,) = layout(text, 200, "shape")
    assert line.text == get_display(shape(text))


def test_marks_follow_their_base_after_reversal():
    (line,) = layout("سَل", 80)
    assert line.text == "\u0644\u0633\u064e"
    (line,) = layout("سَل", 80, "shape")
    assert line.text == "\ufede\ufeb3\u064e"


def test_zwnj_moves_to_the_left_of_its_letter():
    (line,) = layout("می\u200cخ", 80)
    assert line.text == "خ\u200bیم"
    assert ZWNJ not in line.text and line.width == 3


def test_lam_alef_is_kept_legible():
    assert layout("سلام", 80)[0].text == "ماـلس"
    assert layout("سلام", 80)[0].width == 5
    assert layout("سلام", 80, lam_alef="split")[0].text == "ما\u200bلس"
    assert layout("سلام", 80, lam_alef="font")[0].text == "مالس"
    assert layout("لَا", 80)[0].text == "اـلَ"
    assert "ـ" not in layout("ل\u200cا", 80)[0].text
    assert "ـ" not in layout("ل ا", 80)[0].text
    assert "ـ" not in layout("ال", 80)[0].text


def test_caret_on_stretched_lam_alef():
    (line,) = layout("سلام", 80)
    assert [line.caret_column(i) for i in range(5)] == [4, 3, 1, 0, -1]


def test_joiner_after_arabic_letter_does_not_swallow_the_next_one():
    (line,) = layout("ه\u200dب", 80)
    assert line.width == 2 and "\u200d" not in line.text


def test_emoji_are_not_reversed():
    family = "👨" + ZWJ + "👩" + ZWJ + "👧"
    (line,) = layout("س " + family + " 👍🏽", 80)
    assert family in line.text and "👍🏽" in line.text


@pytest.mark.parametrize(
    ("text", "shown"), [("🇮🇷", "IR"), ("1️⃣", "1"), ("#️⃣", "#"), ("1" + chr(0x20E3), "1")]
)
def test_undrawable_sequences_become_plain_text(text, shown):
    (line,) = layout(text, 80)
    assert line.text == shown and line.width == len(shown)
    (line,) = layout("س " + text + " ب", 80)
    assert shown.strip() in line.text
    assert layout(text, 80, "off")[0].text == text


def test_invisible_controls_are_not_drawn():
    (line,) = layout("می\u200cخ\u200f\u202e\u2067\u2069", 80)
    assert not set(line.text) & set("\u200f\u202e\u2067\u2069")


def test_brackets_are_mirrored():
    (line,) = layout("(سلام)", 80)
    assert line.text[0] == "(" and line.text[-1] == ")"


def test_direction():
    assert direction("123 سلام hi") == "rtl"
    assert direction("hi سلام") == "ltr"
    assert direction("123 !") == "ltr"


@pytest.mark.parametrize("mode", ["reorder", "shape", "terminal", "off"])
@pytest.mark.parametrize("width", [1, 2, 5, 9, 40])
@pytest.mark.parametrize("text", SAMPLES)
def test_wrapping_covers_text_and_fits(text, width, mode):
    lines = layout(text, width, mode)
    rebuilt = []
    for i, line in enumerate(lines):
        if i and line.start != lines[i - 1].end:
            rebuilt.append("\n")
        rebuilt.append(text[line.start : line.end])
        widest = max((c.width for c in line.cells), default=0)
        assert line.width <= max(width, widest)
        spans = sorted((c.start, c.end) for c in line.cells)
        assert "".join(text[a:b] for a, b in spans) == text[line.start : line.end]
    assert "".join(rebuilt) == text


@pytest.mark.parametrize("text", SAMPLES)
def test_index_map_round_trips(text):
    for line in layout(text, 12):
        for cell in line.cells:
            if cell.width:
                column = line.caret_column(cell.start)
                assert line.index_at(column) == cell.start


def test_caret_at_end_of_line():
    (rtl,) = layout("سلام", 80)
    (ltr,) = layout("abc", 80)
    assert rtl.caret_column(4) == -1 and rtl.index_at(-1) == 4
    assert ltr.caret_column(3) == 3 and ltr.index_at(3) == 3
    assert rtl.caret_column(0) == rtl.width - 1  # first letter is rightmost
    assert rtl.index_at(99) == 0 and ltr.index_at(-1) == 0


def test_lines_wrap_in_reading_order():
    lines = layout("سلام دنیا خوب", 6)
    assert [line.start for line in lines] == [0, 5, 10]
    assert all(line.rtl for line in lines)


def test_terminal_and_off_modes_leave_text_alone():
    (line,) = layout("سلام", 80, "terminal")
    assert line.text == "سلام" and line.rtl
    (line,) = layout("سلام", 80, "off")
    assert line.text == "سلام" and not line.rtl


@pytest.mark.parametrize("text", ["می\u200cخواهم", "ب\u200c", "\u200cب", "نمی\u200c"])
def test_rows_do_not_start_with_an_invisible_break(text):
    # After a box-drawing character it shifts the row in Windows Terminal.
    for width in (1, 2, 4, 20):
        for line in layout(text, width):
            assert not line.text.startswith("\u200b")
