"""Second RTL experiment: let the terminal join letters, Peyk only reorders.

Plain prints, so this shows the terminal alone, without Textual in between.
"""

import re

from peyk.text.bidi import layout

print("\x1b[2J\x1b[H", end="")

WORDS = ["سلام", "طولانی", "لا", "حال", "الا", "کلاه", "اسلام", "می\u200cخواهم", "کتاب\u200cها", "(سلام)"]
LAM, ALEFS = "ل", "اأإآ"


def visual(text: str, joiner: str = "", zwnj_side: str = "before") -> str:
    """Reordered, unshaped. Buffer runs left to right on screen."""
    (line,) = layout(text, 200)
    out = []
    for cell in line.cells:
        source = text[cell.start : cell.end]
        base = source.replace("\u200c", "")
        if cell.level % 2 and len(base) == 1 and not base.isalpha():
            base = cell.text  # mirrored bracket
        if "\u200c" in source:
            base = "\u200c" + base if zwnj_side == "before" else base + "\u200c"
        out.append(base)
    result = "".join(out)
    if joiner:
        result = re.sub(f"([{ALEFS}])({LAM})", lambda m: m[1] + joiner + m[2], result)
    return result


VARIANTS = [
    ("0 raw, no reorder", lambda t: t),
    ("1 reorder, ZWNJ after", lambda t: visual(t, zwnj_side="after")),
    ("2 reorder, ZWNJ before", lambda t: visual(t)),
    ("3 = 2 + ZWJ in lam-alef", lambda t: visual(t, "\u200d")),
    ("4 = 2 + CGJ in lam-alef", lambda t: visual(t, "\u034f")),
    ("5 = 2 + WJ in lam-alef", lambda t: visual(t, "\u2060")),
    ("6 = 2 + tatweel", lambda t: visual(t, "ـ")),
    ("7 = 2 + ZWJ ZWNJ ZWJ", lambda t: visual(t, "\u200d\u200c\u200d")),
    ("8 A: presentation forms", lambda t: layout(t, 200, "shape")[0].text),
]

for name, make in VARIANTS:
    print(f"{name:26}|" + "| ".join(make(w) for w in WORDS) + "|")
print("\nDouble size:")
for name, make in VARIANTS[2:8]:
    row = f"{name[:1]} |" + " ".join(make(w) for w in WORDS[:7]) + "|"
    print(f"\x1b#3{row}\r\n\x1b#4{row}")

print("\nStyle changes inside a word (highlight, caret):")
word = visual("دنیا")
print(f"plain {word}   colour {word[:2]}\x1b[31m{word[2:]}\x1b[0m   "
      f"reverse {word[0]}\x1b[7m{word[1]}\x1b[0m{word[2:]}   bold {word[:2]}\x1b[1m{word[2:]}\x1b[0m   "
      f"underline {word[:2]}\x1b[4m{word[2:]}\x1b[0m")
input("\nEnter to close")
