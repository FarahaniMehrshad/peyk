"""The sidebar: two lines per chat, drawn on demand so thousands are fine."""

from __future__ import annotations

import time

from rich.style import Style
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from peyk.models import Chat, Kind
from peyk.text import format as fmt
from peyk.ui.widgets.strips import Row, TextOptions, ink

ROWS = 2


class ChatList(Widget):
    COMPONENT_CLASSES = {
        "chatlist--selected", "chatlist--open", "chatlist--title", "chatlist--dim",
        "chatlist--badge", "chatlist--badge-muted", "chatlist--mention", "chatlist--doing",
    }  # fmt: skip
    DEFAULT_CSS = """
    ChatList { height: 1fr; }
    ChatList > .chatlist--selected { background: $surface; }
    ChatList > .chatlist--open { color: $primary; text-style: bold; }
    ChatList > .chatlist--title { text-style: bold; }
    ChatList > .chatlist--dim { color: $text-muted; }
    ChatList > .chatlist--badge { color: $on-primary; background: $primary; }
    ChatList > .chatlist--badge-muted { color: $text-muted; }
    ChatList > .chatlist--doing { color: $accent; }
    ChatList > .chatlist--mention { color: $warning; text-style: bold; }
    """

    class Opened(Message):
        def __init__(self, chat_id: int) -> None:
            super().__init__()
            self.chat_id = chat_id

    def __init__(self, options: TextOptions, id: str) -> None:  # noqa: A002
        super().__init__(id=id)
        self._options = options
        self.chats: list[Chat] = []
        self.selected: int | None = None  # chat id, so reordering keeps it
        self.open_chat: int | None = None
        self.empty_text = "Loading chats..."
        self.doing: dict[int, list[str]] = {}  # what is being typed, by chat
        self._top = 0

    # -- state

    def show(self, chats: list[Chat], open_chat: int | None) -> None:
        old = self._index()
        self.chats, self.open_chat = chats, open_chat
        if self._index() is None and chats:
            self.selected = chats[min(old or 0, len(chats) - 1)].id
        self._keep_visible()
        self.refresh()

    def show_doing(self, doing: dict[int, list[str]]) -> None:
        if doing != self.doing:
            self.doing = doing
            self.refresh()

    def _index(self) -> int | None:
        return next((i for i, c in enumerate(self.chats) if c.id == self.selected), None)

    def select(self, chat_id: int) -> None:
        self.selected = chat_id
        self._keep_visible()
        self.refresh()

    def move(self, step: int) -> None:
        if not self.chats:
            return
        index = self._index() or 0
        self.select(self.chats[max(0, min(len(self.chats) - 1, index + step))].id)

    def go_to_end(self) -> None:
        self.move(len(self.chats))

    def go_to_start(self) -> None:
        self.move(-len(self.chats))

    @property
    def page(self) -> int:
        return max(1, self.size.height // ROWS)

    def _keep_visible(self) -> None:
        index = self._index() or 0
        if index < self._top:
            self._top = index
        elif index >= self._top + self.page:
            self._top = index - self.page + 1
        self._top = max(0, min(self._top, len(self.chats) - self.page))

    def on_resize(self) -> None:
        self._keep_visible()

    # -- drawing

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        index = self._top + y // ROWS
        if not self.chats:
            row = Row(width, self.rich_style)
            if y == 0:
                text, cells = self._options.fit(self.empty_text, width - 1)
                row.space(1).add(text, cells, self._style("dim"))
            return row.strip()
        if index >= len(self.chats):
            return Strip.blank(width, self.rich_style)
        chat = self.chats[index]
        base = self.rich_style
        if chat.id == self.selected:
            base += Style(bgcolor=self.get_component_rich_style("chatlist--selected").bgcolor)
        row = Row(width, base)
        mark = "▌" if chat.id == self.open_chat else " "
        row.add(mark, 1, self._style("open"))
        return (self._title_line if y % ROWS == 0 else self._preview_line)(row, chat)

    def _style(self, name: str):
        return ink(self, f"chatlist--{name}")

    def _title_line(self, row: Row, chat: Chat) -> Strip:
        when = fmt.list_date(chat.last.date) if chat.last else ""
        ticks = ""
        outgoing = chat.last and chat.last.outgoing and not chat.last.service
        if chat.last and outgoing and chat.kind is not Kind.SAVED:
            ticks = "✓✓ " if chat.last.id <= chat.read_outbox else "✓ "
        right = f"{'^ ' if chat.pin else ''}{ticks}{when} "
        title, cells = self._options.fit(chat.title, row.left - len(right) - 1)
        row.add(title, cells, self._style("open" if chat.id == self.open_chat else "title"))
        row.space(row.left - len(right))
        return row.add(right, len(right), self._style("dim")).strip()

    def _preview_line(self, row: Row, chat: Chat) -> Strip:
        badge = ""
        if chat.unread:
            badge = f" {fmt.count(chat.unread)} "
        elif chat.marked_unread:
            badge = " • "
        mention = "@ " if chat.mentions else ""
        room = row.left - len(badge) - len(mention) - 2
        doing = self.doing.get(chat.id)
        if doing:
            end = row.used + room
            for piece in doing:
                text, cells = self._options.fit(piece, end - row.used)
                row.add(text, cells, self._style("doing"))
        else:
            text, cells = self._options.fit(
                fmt.preview(chat.last, chat) if chat.last else "", room
            )
            row.add(text, cells, self._style("dim"))
        row.space(row.left - len(badge) - len(mention) - 1)
        row.add(mention, len(mention), self._style("mention"))
        muted = chat.muted(time.time())
        if muted:
            style = self._style("badge-muted")
        else:
            style = self.get_component_rich_style("chatlist--badge")
        row.add(badge, len(badge), style)
        return row.strip()

    # -- mouse

    def on_click(self, event: events.Click) -> None:
        index = self._top + event.y // ROWS
        if 0 <= index < len(self.chats):
            self.select(self.chats[index].id)
            self.post_message(self.Opened(self.chats[index].id))

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self._scroll(1)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self._scroll(-1)

    def _scroll(self, step: int) -> None:
        self._top = max(0, min(self._top + step, len(self.chats) - self.page))
        self.refresh()
