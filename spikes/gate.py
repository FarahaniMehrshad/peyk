"""Milestone 0: what can this terminal really do?

Run inside Windows Terminal:  uv run python spikes/gate.py
Nothing here touches the network or a Telegram account.
"""

import os
import sys
import unicodedata

from PIL import Image as PILImage
from PIL import ImageDraw
from rich.text import Text

# Must be imported before the app starts: it probes the terminal.
from textual_image.widget import AutoImage, HalfcellImage, SixelImage
from textual_image.widget import Image as BestImage

from textual import events
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Footer, Static, TabbedContent, TabPane

from peyk.text.bidi import VisualLine, layout
from peyk.text.layout_keys import to_us_key
from peyk.text.sanitize import sanitize

WIDTH = 46
SAMPLES = [
    ("Persian", "سلام دنیا، حال شما چطور است؟"),
    ("ZWNJ", "می\u200cخواهم کتاب\u200cها را بخوانم"),
    ("Mixed", "قیمت 123 تومان (USD) است"),
    ("English first", "Hello سلام 2026 world"),
    ("Persian digits", "ساعت ۱۲:۳۰ و ۵۰٪ تخفیف"),
    ("Emoji", "خانواده 👨\u200d👩\u200d👧 و 👍🏽 و ❤️"),
    ("Arabic marks", "سَلامٌ عَلَيكُم"),
    ("Hebrew", "שלום עולם (test)"),
    ("Wrapped", "این یک پیام طولانی است که باید در چند سطر شکسته شود و ترتیب سطرها درست بماند."),
]


def padded(line: VisualLine, content: str) -> str:
    gap = " " * max(0, WIDTH - line.width)
    return gap + content if line.rtl else content + gap


def boxed(text: str, variant: str) -> Text:
    out = Text()
    mode = {"A": "reorder", "B": "shape", "C": "terminal"}[variant]
    for i, line in enumerate(layout(text, WIDTH, mode)):
        content = line.text
        out.append(f"{variant if not i else ' '} │", style="dim")
        out.append(padded(line, content))
        out.append("│\n", style="dim")
    return out


def rtl_page() -> Text:
    out = Text()
    for name, text in SAMPLES:
        out.append(f"{name}\n", style="bold cyan")
        for variant in "ABC":
            out.append_text(boxed(text, variant))
    return out


class Compose(Widget, can_focus=True):
    DEFAULT_CSS = "Compose { height: 12; border: round $accent; padding: 0 1; }"

    def __init__(self) -> None:
        super().__init__()
        self.buffer = ""
        self.caret = 0
        self.last = "press any key"

    def on_key(self, event: events.Key) -> None:
        char = event.character
        if event.key == "left":
            self.caret = max(0, self.caret - 1)
        elif event.key == "right":
            self.caret = min(len(self.buffer), self.caret + 1)
        elif event.key == "backspace":
            if self.caret:
                self.buffer = self.buffer[: self.caret - 1] + self.buffer[self.caret :]
                self.caret -= 1
        elif event.key == "enter":
            self.insert("\n")
        elif char and unicodedata.category(char[0]) not in ("Cc", "Cs"):
            self.insert(char)
        else:
            return
        event.stop()
        shown = "ZWNJ" if char == "\u200c" else repr(char)
        us = to_us_key(char) if char else "-"
        self.last = f"key={event.key}  character={shown}  physical key={us}"
        self.refresh()

    def on_paste(self, event: events.Paste) -> None:
        event.stop()
        self.insert(sanitize(event.text))
        self.refresh()

    def insert(self, text: str) -> None:
        self.buffer = self.buffer[: self.caret] + text + self.buffer[self.caret :]
        self.caret += len(text)

    def render(self) -> Text:
        out = Text(self.last + "\n\n", style="dim")
        lines = layout(self.buffer, WIDTH)
        row = max(i for i, line in enumerate(lines) if line.start <= self.caret)
        for i, line in enumerate(lines):
            # One spare cell on each side so the end-of-line caret always fits.
            body = Text(" " + padded(line, line.text) + " ")
            if i == row:
                indent = WIDTH - line.width if line.rtl else 0
                column = 1 + indent + line.caret_column(self.caret)
                cell = len(body.plain)
                used = 0
                for j, ch in enumerate(body.plain):
                    if used >= column:
                        cell = j
                        break
                    used += Text(ch).cell_len
                body.stylize("reverse", cell, cell + 1)
            out.append_text(body)
            out.append("\n")
        zwnj = self.buffer.count("\u200c")
        out.append(f"\n{len(self.buffer)} characters, {zwnj} ZWNJ, caret {self.caret}", "dim")
        return out


def picture() -> PILImage.Image:
    image = PILImage.new("RGB", (320, 200))
    image.putdata([(x * 255 // 320, y * 255 // 200, 160) for y in range(200) for x in range(320)])
    draw = ImageDraw.Draw(image)
    draw.ellipse((110, 50, 210, 150), fill="white", outline="black", width=4)
    draw.line((0, 0, 320, 200), fill="red", width=3)
    return image


class Gate(App):
    TITLE = "Peyk feasibility gate"
    BINDINGS = [
        ("f1", "page('rtl')", "RTL"),
        ("f2", "page('keys')", "Typing"),
        ("f3", "page('image')", "Image"),
        ("f4", "page('toast')", "Toast"),
        ("f5", "toast", "Send toast"),
        ("f6", "sizes", "Log sizes"),
        ("ctrl+q", "quit", "Quit"),
    ]
    CSS = "Image { width: 40; height: 12; }"

    def compose(self) -> ComposeResult:
        with TabbedContent(id="pages"):
            with TabPane("F1 RTL", id="rtl"):
                with VerticalScroll():
                    yield Static(
                        "Which row reads correctly, A, B or C?  Is the right-hand │ straight?\n"
                        "A = Peyk reorders, terminal joins; B = Peyk does both; C = untouched\n",
                        markup=False,
                    )
                    yield Static(rtl_page())
            with TabPane("F2 Typing", id="keys"):
                yield Static(
                    "Type Persian, then use Left/Right, Backspace, paste, and Shift+Space\n"
                    "for ZWNJ. Left moves back one letter in reading order, not on screen.",
                    markup=False,
                )
                yield Compose()
            with TabPane("F3 Image", id="image"):
                with VerticalScroll():
                    yield Static(f"Best renderer: {renderer()}", markup=False)
                    yield BestImage(picture())
                    yield Static("\nHalf-block fallback:", markup=False)
                    yield HalfcellImage(picture())
                    for i in range(70):
                        yield Static(f"scroll filler {i}", markup=False)
            with TabPane("F4 Toast", id="toast"):
                yield Static("Press F5 to send a Windows notification.", id="toast-result")
        yield Footer()

    def action_page(self, name: str) -> None:
        self.query_one("#pages", TabbedContent).active = name
        if name == "keys":
            self.call_after_refresh(self.query_one(Compose).focus)

    def action_sizes(self) -> None:
        with open(LOG, "a", encoding="utf-8") as log:
            for w in self.query("*"):
                if "Image" in type(w).__name__ or "Scroll" in type(w).__name__:
                    log.write(f"{type(w).__module__}.{type(w).__name__} size={w.size} "
                              f"region={w.region} scroll={w.scroll_offset}\n")

    def action_toast(self) -> None:
        result = self.query_one("#toast-result", Static)
        try:
            from windows_toasts import Toast, WindowsToaster

            WindowsToaster("Peyk").show_toast(Toast(["Peyk", "سلام! می\u200cبینی؟"]))
            result.update("Sent. Did it appear, and is the Persian text right?")
        except Exception as error:
            result.update(Text(f"FAILED: {type(error).__name__}: {error}"))


LOG = os.path.join(os.path.dirname(__file__), "gate.log")


def renderer() -> str:
    if BestImage is SixelImage:
        return "Sixel"
    return getattr(AutoImage._Renderable, "__module__", "unknown").rsplit(".", 1)[-1]


if __name__ == "__main__":
    with open(LOG, "w", encoding="utf-8") as log:
        log.write(
            f"python={sys.version.split()[0]} renderer={renderer()} "
            f"windows_terminal={bool(os.environ.get('WT_SESSION'))}\n"
        )
    Gate().run()
