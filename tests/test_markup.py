import pytest

from peyk.text.markup import parse, revise, utf16_length


def spans(text):
    parsed = parse(text)
    return parsed.text, [(parsed.text[s.start : s.end], s.kind) for s in parsed.spans]


@pytest.mark.parametrize(
    ("typed", "text", "found"),
    [
        ("plain", "plain", []),
        ("**bold**", "bold", [("bold", "bold")]),
        ("a **b c** d", "a b c d", [("b c", "bold")]),
        ("__it__, ~~no~~ ||secret||!", "it, no secret!",
         [("it", "italic"), ("no", "strike"), ("secret", "spoiler")]),
        ("**__both__**", "both", [("both", "bold"), ("both", "italic")]),
        ("**bold __and italic__ here**", "bold and italic here",
         [("bold and italic here", "bold"), ("and italic", "italic")]),
        ("(**x**)", "(x)", [("x", "bold")]),
        ("**سلام** دنیا", "سلام دنیا", [("سلام", "bold")]),
        ("**سلام**، خوبی؟", "سلام، خوبی؟", [("سلام", "bold")]),
        ("**👍🏽** ok", "👍🏽 ok", [("👍🏽", "bold")]),
        ("run `uv sync` now", "run uv sync now", [("uv sync", "code")]),
        ("`**x**`", "**x**", [("**x**", "code")]),
        ("```\na = 1\nb = 2\n```", "a = 1\nb = 2", [("a = 1\nb = 2", "pre")]),
        ("look ```x``` here", "look x here", [("x", "pre")]),
        ("[docs](https://a.b/c?d=1) ok", "docs ok", [("docs", "link")]),
        ("  **x**  \n", "x", [("x", "bold")]),
    ],
)  # fmt: skip
def test_formatting(typed, text, found):
    assert spans(typed) == (text, found)


@pytest.mark.parametrize(
    "text",
    [
        "a__b__c", "__init__.py", "C:\\x\\__init__", "2 ** 3 ** 4", "x**y**z", "** not **",
        "****", "__", "a || b || c", "`", "``", "` `", "```", "```\n```", "[x](javascript:alert)",
        "[x]()", "**open", "**a\n\nb**", "tic`tac\ntoe`", "~~ ~~", "||||",
    ],
)  # fmt: skip
def test_left_alone(text):
    assert spans(text) == (text, [])


def test_language_of_a_block():
    parsed = parse("```python\nprint(1)\n```")
    assert parsed.text == "print(1)"
    assert parsed.spans[0].url == "python"


def test_where_maps_every_index():
    typed = " **a** b"
    parsed = parse(typed)
    assert parsed.text == "a b"
    assert len(parsed.where) == len(typed) + 1
    assert [parsed.where[typed.index(c)] for c in "ab"] == [0, 2]
    assert parsed.where[-1] == 3


@pytest.mark.parametrize("seed", range(30))
def test_any_input_is_survived(seed):
    import random

    rng = random.Random(seed)
    text = "".join(rng.choices("ab *_~|`[]()\n.س👍", k=60))
    parsed = parse(text)
    for span in parsed.spans:
        assert 0 <= span.start < span.end <= len(parsed.text)
    revised = revise(text, [(3, 9), (20, 21)], text[:10] + "**x** " + text[15:])
    for place in revised.carried:
        assert place is None or 0 <= place[0] < place[1] <= len(revised.text)


def kept(old, part, new, **options):
    start = old.index(part)
    revised = revise(old, [(start, start + len(part))], new, **options)
    place = revised.carried[0]
    return revised.text, place and revised.text[slice(*place)]


def test_edit_keeps_formatting_with_its_words():
    assert kept("say hello there", "hello", "now say hello there!") == (
        "now say hello there!", "hello")  # fmt: skip
    assert kept("say hello there", "hello", "say hallo there")[1] == "hallo"
    assert kept("say hello there", "hello", "say there") == ("say there", None)
    assert kept("teh word is bold hree", "word", "the word is bold here")[1] == "word"


def test_edit_takes_new_marks_but_leaves_old_literals():
    revised = revise("use **kw** here", [], "use **kw** **right** here")
    assert revised.text == "use **kw** right here"
    assert [(s.kind, revised.text[s.start : s.end]) for s in revised.spans] == [("bold", "right")]


def test_edit_shifts_kept_ranges_past_removed_marks():
    assert kept("first link", "link", "**first** link") == ("first link", "link")


def test_edit_without_formatting():
    revised = revise("a", [], "a **b**", formatting=False)
    assert revised.text == "a **b**" and not revised.spans


def test_utf16_length():
    assert utf16_length("aس👍") == 4
