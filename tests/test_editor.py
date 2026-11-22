import random

import pytest

from peyk.text.bidi import ZWNJ
from peyk.text.editor import Editor


def editor(text="", caret=None, width=40):
    e = Editor()
    e.width = width
    e.set(text)
    if caret is not None:
        e.caret = caret
    return e


def test_typing_and_deleting():
    e = editor()
    for char in "سلام":
        e.insert(char)
    assert (e.text, e.caret) == ("سلام", 4)
    e.backspace()
    assert e.text == "سلا"
    e.start_of_text()
    e.delete()
    assert e.text == "لا"


def test_arrows_follow_the_screen():
    e = editor("abc", 1)
    e.right()
    assert e.caret == 2
    e = editor("سلام", 1)
    e.left()
    assert e.caret == 2
    e.right()
    assert e.caret == 1


def test_arrows_in_mixed_text():
    e = editor("سلام abc", 0)
    columns = []
    for _ in range(9):
        columns.append(e.caret_column)
        e.left()
    # Right to left across the whole line, one character at a time.
    assert columns == sorted(columns, reverse=True) and len(set(columns)) == 9
    assert e.caret_column == columns[-1]


def test_zwnj_can_be_typed_and_removed_alone():
    e = editor("می")
    e.insert(ZWNJ)
    e.insert("خ")
    assert e.text == "می" + ZWNJ + "خ"
    e.backspace()
    e.backspace()
    assert e.text == "می"


def test_backspace_takes_marks_one_by_one_and_emoji_whole():
    e = editor("سَ")
    e.backspace()
    assert e.text == "س"
    for emoji in ("👨\u200d👩\u200d👧", "👍🏽", "❤️", "🇮🇷", "1️⃣"):
        e = editor("a" + emoji)
        e.backspace()
        assert e.text == "a"


def test_lines():
    e = editor("one\ntwo")
    e.home()
    assert e.caret == 4
    e.backspace()
    assert e.text == "onetwo"
    e.undo()
    assert e.text == "one\ntwo" and e.caret == 4
    e.up()
    e.end()
    e.delete()
    assert e.text == "onetwo"


def test_up_and_down_keep_the_column():
    e = editor("long first line\nx\nlong third line", 10)
    e.down()
    assert e.caret == len("long first line\nx")
    e.down()
    assert e.caret == len("long first line\nx\n") + 10
    assert not e.down()
    e.up()
    e.up()
    assert e.caret == 10
    assert not e.up()


def test_wrapped_rows():
    e = editor("aaa bbb ccc", 0, width=5)
    assert [r.start for r in e.rows] == [0, 4, 8]
    e.end()
    assert e.caret == 3 and e.caret_row == 0
    e.right()
    assert e.caret == 4 and e.caret_row == 1
    e.left()
    assert e.caret_row == 0


@pytest.mark.parametrize("width", [4, 7, 12])
@pytest.mark.parametrize("text", [
    "aaa bbb ccc ddd", "سلام دنیا خوب است", "سلام abc دنیا 123 xyz", "abc سلام\nدنیا def\n\nx",
    "می\u200cخواهم کتاب\u200cها را", "aaaaaaaaaaaaaaaa", "سسسسسسسسسسسسسس",
])  # fmt: skip
def test_walking_visits_every_row_and_stays_inside(text, width):
    e = editor(text, 0, width)
    seen = {0}
    for _ in range(6 * len(text)):
        before = e.caret
        step = e.left if e.rows[e.caret_row].rtl else e.right
        step()
        assert 0 <= e.caret_column <= width
        seen.add(e.caret_row)
        if e.caret == before:
            break
    assert seen == set(range(len(e.rows)))
    assert e.caret_row == len(e.rows) - 1


def test_caret_after_the_last_letter_has_a_column():
    for text in ("a" * 9, "س" * 9):
        e = editor(text, width=10)
        assert 0 <= e.caret_column < 10
        assert len(e.rows) == 1
    assert editor("س", width=10).caret_column == 8
    assert editor("س", 0, width=10).caret_column == 9


def test_clicking():
    e = editor("abc\nسلام", width=20)
    e.click(0, 1)
    assert e.caret == 1
    e.click(0, 15)
    assert e.caret == 3
    e.click(1, 19)
    assert e.caret == 4
    e.click(1, 0)
    assert e.caret == 8
    e.click(99, 0)
    assert e.caret == 8


def test_words():
    e = editor("one two, three")
    e.word_left()
    assert e.caret == 9
    e.word_left()
    assert e.caret == 4
    e.word_right()
    assert e.caret == 9
    e.delete_word()
    assert e.text == "one three"
    e = editor("سلام می\u200cخواهم", 0)
    e.word_left()
    assert e.caret == 5
    e.word_left()
    assert e.caret == len(e.text)
    e.word_right()
    assert e.caret == 5


def test_killing_lines():
    e = editor("one\ntwo three", 8)
    e.delete_to_end()
    assert e.text == "one\ntwo "
    e.delete_to_start()
    assert e.text == "one\n"


def test_undo_groups_words():
    e = editor()
    for char in "hello big world":
        e.insert(char)
    e.undo()
    assert e.text == "hello big "
    e.undo()
    assert e.text == "hello "
    e.redo()
    assert e.text == "hello big "
    e.insert("x")
    e.redo()
    assert e.text == "hello big x"
    e.insert("pasted text")
    e.undo()
    assert e.text == "hello big x"


@pytest.mark.parametrize("seed", range(40))
def test_random_keys_never_break_it(seed):
    rng = random.Random(seed)
    e = editor("", width=rng.choice([1, 2, 5, 20]))
    actions = [e.left, e.right, e.up, e.down, e.home, e.end, e.backspace, e.delete, e.word_left,
               e.word_right, e.delete_word, e.undo, e.redo, e.delete_to_start, e.delete_to_end,
               e.start_of_text, e.end_of_text]  # fmt: skip
    letters = ["a", "b", " ", "س", "ل", "ا", "\n", ZWNJ, "\u200d", "👍", "\U0001f3fd", "1", "(",
               "\u064e", "é", "\u0301", "🇮", "🇷", "漢", "ש"]  # fmt: skip
    for _ in range(300):
        if rng.random() < 0.6:
            e.insert(rng.choice(letters))
        else:
            rng.choice(actions)()
        assert 0 <= e.caret <= len(e.text)
        assert 0 <= e.caret_row < len(e.rows)
        assert 0 <= e.caret_column <= max(e.width, 2)
