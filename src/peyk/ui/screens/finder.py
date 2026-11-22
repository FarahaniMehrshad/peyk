"""Popups that search while you type: chats, messages and senders."""

from __future__ import annotations

from collections.abc import AsyncIterator

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Static

from peyk.backend.base import Backend, BackendError
from peyk.models import Chat, Found, Kind, Message, User
from peyk.text import format as fmt
from peyk.text.match import fold, ranked
from peyk.ui.widgets.edit_box import EditBox
from peyk.ui.widgets.results import Entry, ResultList
from peyk.ui.widgets.strips import TextOptions

TYPING_PAUSE = 0.35  # before asking the server
MIN_QUERY = 2
STEPS = {
    "up": -1, "ctrl+p": -1, "ctrl+k": -1, "down": 1, "ctrl+n": 1, "ctrl+j": 1,
    "pageup": -8, "pagedown": 8,
}  # fmt: skip


class Finder[T](ModalScreen[T | None]):
    HINTS = "Up/Down: move    Enter: open    Esc: close"

    def __init__(self, backend: Backend, options: TextOptions, title: str) -> None:
        super().__init__()
        self._backend = backend
        self._options = options
        self._title = title
        self._pause: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="frame"), Vertical(classes="finder"):
            yield EditBox(self._options, id="query", placeholder="Type to search")
            yield ResultList(self._options)
            yield Static(self.HINTS, markup=False, classes="finder-hints")

    def on_mount(self) -> None:
        self._set_title(self._title)
        self.query_one("#query").focus()
        self.refresh_results()

    def _set_title(self, title: str) -> None:
        text, _ = self._options.fit(title, 60)
        # Escaped: chat names are not markup.
        self.query_one(".finder").border_title = text.replace("[", r"\[")

    @property
    def typed(self) -> str:
        return self.query_one("#query", EditBox).text.strip()

    @property
    def results(self) -> ResultList:
        return self.query_one(ResultList)

    def refresh_results(self) -> None:
        """Show what is known for the query right now."""

    async def ask_server(self, query: str) -> None:
        """Then add what the server knows."""

    @on(EditBox.Changed)
    def _query_changed(self) -> None:
        self.refresh_results()

    def ask_soon(self) -> None:
        self.stop_asking()
        self._pause = self.set_timer(TYPING_PAUSE if self.typed else 0.01, self._ask_now)

    def stop_asking(self) -> None:
        if self._pause:
            self._pause.stop()
        self.workers.cancel_group(self, "ask")

    def _ask_now(self) -> None:
        self.run_worker(self._ask(), group="ask", exclusive=True)

    async def _ask(self) -> None:
        try:
            await self.ask_server(self.typed)
        except BackendError as error:
            if self.is_attached:
                self.results.set_note(error.message)

    @on(EditBox.Submitted)
    @on(ResultList.Chosen)
    def _choose(self) -> None:
        entry = self.results.chosen
        if entry:
            self.dismiss(entry.value)  # type: ignore[arg-type]

    @on(EditBox.Left)
    def _close(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        step = STEPS.get(event.key)
        if step:
            event.stop()
            event.prevent_default()
            self.results.move(step)
        elif event.key == "tab":
            event.stop()
            event.prevent_default()
            self.toggle()
        elif event.key == "escape":
            event.stop()
            self.dismiss(None)

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:  # outside the box
            self.dismiss(None)

    def toggle(self) -> None:
        pass


class ChatFinder(Finder[Chat]):
    def __init__(self, backend: Backend, options: TextOptions, chats: list[Chat]) -> None:
        super().__init__(backend, options, "Find a chat")
        self._chats = chats

    def refresh_results(self) -> None:
        query = self.typed
        found = ranked(query, self._chats, lambda c: (c.title, c.username))
        server = len(fold(query).removeprefix("@")) >= MIN_QUERY
        note = "Looking in your contacts..." if server else "" if found else "No chats."
        self.results.show([self._entry(c) for c in found], note)
        if server:
            self.ask_soon()
        else:
            self.stop_asking()

    def _entry(self, chat: Chat) -> Entry:
        right = fmt.KINDS[chat.kind]
        if chat.archived:
            right = f"archived {right}"
        if chat.unread:
            right = f"{fmt.count(chat.unread)} unread  {right}"
        return Entry(chat.title, chat, right=right)

    async def ask_server(self, query: str) -> None:
        known = {c.id for c in self._chats}
        more = [c for c in await self._backend.find_chats(query) if c.id not in known]
        entries = [Entry(c.title, c, right=f"{fmt.KINDS[c.kind]}, no chat yet") for c in more]
        note = "" if self.results.entries or entries else "Nothing found."
        self.results.extend(entries, note)


class MessageFinder(Finder[Found]):
    HINTS = "Up/Down: move    Enter: go to message    Esc: close"
    SCOPE_HINTS = "Up/Down: move    Enter: go to message    Tab: this/all chats    Esc: close"

    def __init__(
        self,
        backend: Backend,
        options: TextOptions,
        chat: Chat | None,
        everywhere: bool = False,
        sender: User | None = None,
    ) -> None:
        super().__init__(backend, options, "")
        self._chat = chat
        self._sender = sender
        self._everywhere = everywhere or chat is None
        self._pages: AsyncIterator[list[Found]] | None = None
        self._loading = False

    def on_mount(self) -> None:
        self._title = self._describe()
        if self._can_toggle:
            self.query_one(".finder-hints", Static).update(self.SCOPE_HINTS)
        super().on_mount()

    @property
    def _can_toggle(self) -> bool:
        return self._chat is not None and self._sender is None

    def _describe(self) -> str:
        if self._sender and self._chat:
            return f"Messages from {self._sender.name} in {self._chat.title}"
        if self._everywhere or not self._chat:
            return "Search all chats"
        return f"Search in {self._chat.title}"

    def toggle(self) -> None:
        if self._can_toggle:
            self._everywhere = not self._everywhere
            self._set_title(self._describe())
            self.refresh_results()

    def refresh_results(self) -> None:
        self._pages = None
        if len(self.typed) < MIN_QUERY and not self._sender:
            self.stop_asking()
            self.results.show([], "Type at least two letters.")
            return
        self.results.show([], "Searching...")
        self.ask_soon()

    async def ask_server(self, query: str) -> None:
        chat_id = None if self._everywhere else self._chat.id  # type: ignore[union-attr]
        sender_id = self._sender.id if self._sender else None
        self._pages = self._backend.search(query, chat_id, sender_id).__aiter__()
        await self._next_page(first=True)

    @on(ResultList.NearEnd)
    def _more(self) -> None:
        if self._pages is not None and not self._loading:
            self.run_worker(self._load_more(), group="ask", exclusive=True)

    async def _load_more(self) -> None:
        try:
            await self._next_page()
        except BackendError as error:
            self._pages = None
            self.results.set_note(error.message)

    async def _next_page(self, first: bool = False) -> None:
        assert self._pages is not None
        self._loading = True
        try:
            page = await anext(self._pages, None)
        finally:
            self._loading = False
        if page is None:
            self._pages = None
            note = "" if self.results.entries else "Nothing found."
            self.results.set_note(note)
            return
        entries = [self._entry(found) for found in page]
        if first:
            self.results.show(entries)
        else:
            self.results.extend(entries)

    def _entry(self, found: Found) -> Entry:
        message = found.message
        who = "You" if message.outgoing else message.sender
        chat = found.chat
        title = chat.title if chat else who
        if chat is None or chat.kind in (Kind.SAVED, Kind.CHANNEL) or chat.title == who:
            who = ""
        text = fmt.snippet(message, self.typed) or " "
        return Entry(title, found, who, fmt.list_date(message.date), text)


class SenderFinder(Finder[User]):
    HINTS = "Up/Down: move    Enter: show messages    Esc: close"

    def __init__(
        self, backend: Backend, options: TextOptions, chat: Chat, known: list[User]
    ) -> None:
        super().__init__(backend, options, f"Find a sender in {chat.title}")
        self._chat = chat
        self._known = known

    def refresh_results(self) -> None:
        found = ranked(self.typed, self._known, lambda u: (u.name, u.username))
        ask = self._chat.kind is Kind.GROUP
        note = "Looking at the members..." if ask else "" if found else "Nobody found."
        self.results.show([self._entry(u) for u in found], note)
        if ask:
            self.ask_soon()

    def _entry(self, user: User) -> Entry:
        handle = f"@{user.username}" if user.username else ""
        return Entry(user.name or "Unknown", user, right=handle)

    async def ask_server(self, query: str) -> None:
        known = {u.id for u in self._known}
        people = await self._backend.members(self._chat.id, query.removeprefix("@"))
        entries = [self._entry(u) for u in people if u.id not in known]
        note = "" if self.results.entries or entries else "Nobody found."
        self.results.extend(entries, note)


def senders(messages: list[Message], me: User | None) -> list[User]:
    """Who wrote lately, most recent first."""
    found: dict[int, User] = {}
    for message in reversed(messages):
        if message.service or not message.sender_id or message.sender_id in found:
            continue
        name = "You" if message.outgoing else message.sender
        found[message.sender_id] = User(message.sender_id, name)
    if me and me.id not in found:
        found[me.id] = User(me.id, "You")
    return list(found.values())
