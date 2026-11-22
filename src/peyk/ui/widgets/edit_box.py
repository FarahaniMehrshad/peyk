"""Text input that can show and edit right-to-left text, with ZWNJ and pasting."""

from __future__ import annotations

from unicodedata import category

from rich.style import Style
from textual import events
from textual.geometry import Offset, Size
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from peyk.text.bidi import VisualLine
from peyk.text.editor import Editor
from peyk.text.sanitize import sanitize, sanitize_line
from peyk.ui.widgets.strips import Row, TextOptions, ink

# Enter sends; Windows Terminal reports Ctrl+Enter as Ctrl+J.
_NEWLINES = frozenset({"ctrl+j", "ctrl+enter", "shift+enter", "alt+enter", "newline"})


class EditBox(Widget, can_focus=True):
    COMPONENT_CLASSES = {"editbox--caret", "editbox--dim", "editbox--header"}
    DEFAULT_CSS = """
    EditBox { height: auto; }
    EditBox > .editbox--caret { color: $on-primary; background: $primary; }
    EditBox > .editbox--dim { color: $text-muted; }
    EditBox > .editbox--header { color: $secondary; }
    """

    class _FromBox(Message):
        def __init__(self, box: EditBox) -> None:
            super().__init__()
            self.box = box

        @property
        def control(self) -> EditBox:
            return self.box

    class Changed(_FromBox):
        pass

    class Submitted(_FromBox):
        pass

    class Emptied(_FromBox):
        """Backspace with nothing left to delete."""

    class Left(_FromBox):
        """Escape was pressed."""

    class Clicked(_FromBox):
        pass

    def __init__(
        self,
        options: TextOptions,
        id: str,  # noqa: A002
        *,
        placeholder: str = "",
        max_rows: int = 1,
    ) -> None:
        super().__init__(id=id)
        self._options = options
        self.editor = Editor(options.mode, options.lam_alef)
        self.placeholder = placeholder
        self.max_rows = max_rows
        self.header = ""  # a line above the text
        self.frozen = False  # shown but not editable
        self._top = 0

    # -- content

    @property
    def text(self) -> str:
        return self.editor.text

    @property
    def multiline(self) -> bool:
        return self.max_rows > 1

    def set(self, text: str) -> None:
        self.editor.set(text)
        self._show()

    def set_header(self, header: str) -> None:
        if header != self.header:
            self.header = header
            self._show()

    def set_placeholder(self, placeholder: str) -> None:
        if placeholder != self.placeholder:
            self.placeholder = placeholder
            self.refresh()

    def insert(self, text: str) -> None:
        text = sanitize(text) if self.multiline else sanitize_line(text)
        if text and not self.frozen:
            self.editor.insert(text)
            self._changed()

    def _changed(self) -> None:
        self._show()
        self.post_message(self.Changed(self))

    def _show(self) -> None:
        self._scroll()
        rows = self._rows_shown + bool(self.header)
        if self.styles.height is None or rows != self.content_size.height:
            self.refresh(layout=True)
        else:
            self.refresh()
        self._place_cursor()

    # -- size and scrolling

    @property
    def _rows_shown(self) -> int:
        return max(1, min(self.max_rows, len(self.editor.rows)))

    def get_content_width(self, container: Size, viewport: Size) -> int:
        return container.width

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        self.editor.width = max(2, width)
        return self._rows_shown + bool(self.header)

    def _scroll(self) -> None:
        row, shown = self.editor.caret_row, self._rows_shown
        self._top = max(row - shown + 1, min(self._top, row))
        self._top = max(0, min(self._top, len(self.editor.rows) - shown))

    def on_resize(self, event: events.Resize) -> None:
        self.editor.width = max(2, self.content_size.width)
        self._scroll()
        self.refresh(layout=True)

    # -- drawing

    def _style(self, name: str) -> Style:
        return ink(self, f"editbox--{name}")

    def render_line(self, y: int) -> Strip:
        width = self.content_size.width
        if self.editor.width != max(2, width):
            self.editor.width = max(2, width)
            self._scroll()
        row = Row(width, self.rich_style)
        if self.header:
            if y == 0:
                text, cells = self._options.fit(self.header, width)
                return row.add(text, cells, self._style("header")).strip()
            y -= 1
        if not self.text and y == 0:
            return self._empty(row)
        index = self._top + y
        if index >= len(self.editor.rows):
            return row.strip()
        return self._draw(row, self.editor.rows[index], index == self.editor.caret_row)

    def _empty(self, row: Row) -> Strip:
        if self.has_focus:
            row.add(" ", 1, self._caret)
        text, cells = self._options.fit(self.placeholder, row.left)
        return row.add(text, cells, self._style("dim")).strip()

    @property
    def _caret(self) -> Style:
        return self.get_component_rich_style("editbox--caret")

    def _draw(self, row: Row, line: VisualLine, has_caret: bool) -> Strip:
        style = self._style("dim") if self.frozen else None
        pad = self.editor.pad(line)
        caret = line.caret_column(self.editor.caret) if has_caret and self.has_focus else None
        if caret == -1:
            row.space(pad - 1).add(" ", 1, self._caret)
        else:
            row.space(pad)
        text, cells, column = "", 0, 0
        for cell in line.cells:
            if cell.width and column == caret:
                row.add(text, cells, style)
                row.add(cell.text, cell.width, self._caret)
                text, cells = "", 0
            else:
                text += cell.text
                cells += cell.width
            column += cell.width
        row.add(text, cells, style)
        if caret == column and caret is not None:
            row.add(" ", 1, self._caret)
        return row.strip()

    def _place_cursor(self) -> None:
        """Where Windows puts its emoji panel and input methods."""
        if self.has_focus:
            row = self.editor.caret_row - self._top + bool(self.header)
            x, y, _, _ = self.content_region
            self.app.cursor_position = Offset(x + self.editor.caret_column, y + row)

    def on_focus(self) -> None:
        self._place_cursor()

    # -- input

    def on_paste(self, event: events.Paste) -> None:
        event.stop()
        self.insert(event.text)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        offset = event.get_content_offset(self)
        if offset:
            row = offset.y - bool(self.header) + self._top
            self.editor.click(row, offset.x)
            self._show()
        self.post_message(self.Clicked(self))

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "escape":
            self._taken(event)
            self.post_message(self.Left(self))
            return
        if key == "enter":
            self._taken(event)
            if not self.frozen:
                self.post_message(self.Submitted(self))
            return
        if self.frozen:
            self._taken(event)
            return
        if key in _NEWLINES:
            if self.multiline:
                self._taken(event)
                self.editor.insert("\n")
                self._changed()
            return
        if self._move(key):
            self._taken(event)
            self._show()
        elif self._change(event) or self._type(event):
            self._taken(event)

    def _taken(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()

    def _move(self, key: str) -> bool:
        edit = self.editor
        match key:
            case "left":
                edit.left()
            case "right":
                edit.right()
            case "ctrl+left":
                edit.word_left()
            case "ctrl+right":
                edit.word_right()
            case "home" | "ctrl+a":
                edit.home()
            case "end" | "ctrl+e":
                edit.end()
            case "ctrl+home":
                edit.start_of_text()
            case "ctrl+end":
                edit.end_of_text()
            case "up" if self.multiline:
                edit.up()
            case "down" if self.multiline:
                edit.down()
            case _:
                return False
        return True

    def _change(self, event: events.Key) -> bool:
        key = event.key
        edit = self.editor
        actions = {
            "backspace": edit.backspace,
            "ctrl+h": edit.backspace,
            "delete": edit.delete,
            "ctrl+d": edit.delete,
            "ctrl+w": edit.delete_word,
            "ctrl+backspace": edit.delete_word,
            "ctrl+u": edit.delete_to_start,
            "ctrl+k": edit.delete_to_end,
            "ctrl+z": edit.undo,
            "ctrl+y": edit.redo,
        }
        if key == "backspace" and event.character == "\x08":
            key = "ctrl+backspace"  # plain Backspace is 0x7f
        if key not in actions or (key == "ctrl+k" and not self.multiline):
            return False
        before = self.text
        if key in ("backspace", "ctrl+h") and not before:
            self.post_message(self.Emptied(self))
        actions[key]()
        if self.text != before:
            self._changed()
        return True

    def _type(self, event: events.Key) -> bool:
        char = event.character
        if event.key.startswith(("alt+", "ctrl+")):
            return False
        # Not `isprintable`: that would refuse ZWNJ, which Persian needs.
        if not char or category(char[0]) in ("Cc", "Cs"):
            return False
        self.editor.insert(sanitize(char))
        self._changed()
        return True
