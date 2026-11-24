"""The main screen: folders and chats on the left, conversations on the right."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from pathlib import Path

import numpy as np
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message as UiMessage
from textual.timer import Timer
from textual.worker import Worker

from peyk.audio import Audio, SoundDevice
from peyk.backend.base import (
    Backend,
    BackendError,
    Event,
    InboxRead,
    Link,
    MessagesRemoved,
    Unsubscribe,
)
from peyk.config import Config
from peyk.files import default_folder
from peyk.keys import hints, pretty
from peyk.models import Chat, State, User
from peyk.outbox import Outbox
from peyk.platform import notify
from peyk.platform.graphics import Graphics
from peyk.platform.notify import Notifier
from peyk.store import ALL, Change, Store
from peyk.ui.alerts import Alerts
from peyk.ui.gallery import Gallery
from peyk.ui.keymap import action_for, pressed
from peyk.ui.screens.confirm import ConfirmScreen
from peyk.ui.screens.extras import LOOK_EVERY, Extras
from peyk.ui.screens.finding import Finding
from peyk.ui.screens.managing import Managing
from peyk.ui.screens.media import MediaActions
from peyk.ui.screens.voice import VoiceMessages
from peyk.ui.screens.writing import Draft, Writing
from peyk.ui.widgets.chat_list import ChatList
from peyk.ui.widgets.conversation import Conversation
from peyk.ui.widgets.divider import Divider
from peyk.ui.widgets.edit_box import EditBox
from peyk.ui.widgets.pin_bar import PinBar
from peyk.ui.widgets.status import StatusBar
from peyk.ui.widgets.strips import TextOptions
from peyk.ui.widgets.tabs import Tab, TabBar

log = logging.getLogger(__name__)
PAGE = 50
MAX_UNREAD_PAGE = 100
MAX_TABS = 9
NARROW = 70
READ_DELAY = 0.8
NOTICE_TIME = 6
MAX_BOX_ROWS = 8
_LIST_HINTS = (
    ("down up", "move"), ("open", "open"), ("find_chat", "find"), ("help", "keys"),
    ("quit", "quit"),
)  # fmt: skip
_CHAT_HINTS = (
    ("down up", "select"), ("write", "write"), ("reply", "reply"), ("react", "react"),
    ("open_media", "open"), ("attach", "attach"), ("help", "keys"),
)  # fmt: skip
_WRITE_HINTS = "Enter send  Ctrl+Enter new line  Esc done"


class MainScreen(Writing, Finding, MediaActions, Extras, Managing, VoiceMessages):
    AUTO_FOCUS = ""
    PAGE = PAGE

    class LoggedOut(UiMessage):
        pass

    def __init__(
        self,
        backend: Backend,
        config: Config | None = None,
        graphics: Graphics | None = None,
        notifier: Notifier | None = None,
        audio: Audio | None = None,
        config_path: Path | None = None,
    ) -> None:
        """`config_path`: where settings changed in Peyk are kept; None keeps them for this run."""
        super().__init__()
        config = config or Config()
        self._backend = backend
        self._audio: Audio = audio or SoundDevice()  # touches no device until asked
        self._decoded: OrderedDict[tuple[int, int], tuple[np.ndarray, int]] = OrderedDict()
        self._options = TextOptions(config.rtl, config.lam_alef, config.maps)  # type: ignore[arg-type]
        self._formatting = config.formatting
        self._keys = config.keys
        self._me: User | None = None
        self._drafts: dict[int, Draft] = {}
        self.store = Store()
        self.outbox = Outbox(
            backend,
            lambda work: self.run_worker(work, group="outbox"),
            self.set_timer,
            self._outbox_changed,
            self._was_sent,
            self._upload_progress,
        )
        self.gallery = Gallery(
            backend,
            graphics or Graphics(),
            lambda work: self.run_worker(work, group="pictures"),
            self._pictures_changed,
        )
        self._inline = config.inline_images and self.gallery.graphics.on
        self.folder_for_files = (
            Path(config.folder).expanduser() if config.folder else default_folder()
        )
        self.progress: dict[tuple[int, int], str] = {}  # of downloads and uploads
        self._progress_shown = 0.0
        self._downloads: dict[tuple[int, int], Worker] = {}
        self._last_folder: Path | None = None
        self._show_typing_to_others = config.show_typing
        self._told_typing: dict[int, float] = {}  # when, by chat
        self._file_memory: dict[str, str] = {}  # the sticker pack open last
        self._forward_tokens: dict[tuple[int, int, int, bool], int] = {}
        self._pin_index: dict[int, int] = {}  # the pinned message shown, from the newest
        self._places: list[tuple[int, int]] = []  # where jumps came from; 0 is the newest
        self._typing_timer: Timer | None = None
        self._alerts: Alerts | None = None
        self._window = 0  # of the terminal
        self._notifier = notifier
        self._show_content = config.show_content
        self._config_path = config_path
        if config.notifications:
            self._start_alerts()
        self.folder = ALL
        self.open_chats: list[int] = []
        self.active: int | None = None
        self.in_chat = False  # which side the keys go to
        self.fullscreen = False
        self._loading_older: set[int] = set()
        self._loading_newer: set[int] = set()
        self._notice_timer: Timer | None = None
        self._read_timer: Timer | None = None
        self._was_online = False
        self._loaded_once = False
        self._load_trouble = False  # a notice says that loading failed
        self._unsubscribe: list[Unsubscribe] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="panels"):
            with Vertical(id="sidebar", classes="panel"):
                yield TabBar(self._options, id="folders")
                yield ChatList(self._options, id="chats")
            with Vertical(id="conversation", classes="panel"):
                yield TabBar(self._options, id="tabs", empty="No open chats")
                yield PinBar(self._options)
                yield Conversation(
                    self._options, id="messages", gallery=self.gallery if self._inline else None
                )
                yield Divider(self._options)
                yield EditBox(self._options, id="compose", max_rows=MAX_BOX_ROWS)
        yield StatusBar()

    @property
    def chat_list(self) -> ChatList:
        return self.query_one(ChatList)

    @property
    def conversation(self) -> Conversation:
        return self.query_one(Conversation)

    def on_mount(self) -> None:
        self._unsubscribe = [
            self._backend.on_link(self._link_changed),
            self._backend.on_event(self._event),
        ]
        self._was_online = self._backend.link is Link.ONLINE
        self.query_one(StatusBar).link = self._backend.link
        self.conversation.progress = self.progress
        self.conversation.failed_hints = hints(
            self._keys, ("open", "try again"), ("edit", "back to writing"), ("delete", "discard")
        )
        self.conversation.close(self._invitation)
        self.watch(self.app, "app_focus", self._focus_changed, init=False)
        self.set_interval(LOOK_EVERY, self._look_for_reactions)
        if self._alerts:
            self._window = notify.terminal_window()
        self._show_draft()
        self._arrange()
        self._load()

    def on_unmount(self) -> None:
        self._stop_voice()
        while self._unsubscribe:
            self._unsubscribe.pop()()

    @property
    def _invitation(self) -> str:
        key = pretty(self._keys["open"])
        return f"Choose a chat and press {key}." if key else "Choose a chat."

    @property
    def _gone(self) -> bool:
        """The screen is being removed; background work may still be finishing."""
        return not self.is_attached or not self.query(StatusBar)

    # -- loading ------------------------------------------------------------

    def _load(self) -> None:
        self.run_worker(self._load_everything(), group="load", exclusive=True)

    async def _load_everything(self) -> None:
        for attempt in range(3):
            try:
                self._me = await self._backend.me()
                await self._load_folders()
                await self._load_chats()
                await self._refresh_open_chat()
                if self._load_trouble:
                    self._load_trouble = False
                    self._say("")
                return
            except BackendError as error:
                wait = error.retry_after or 5 * (attempt + 1)
                self._load_trouble = True
                self._say(f"{error.message} Trying again in {wait}s.")
                await asyncio.sleep(wait)
        self._say("Chats could not be loaded. They will load when the connection is back.")

    async def _load_folders(self) -> None:
        self.store.folders = await self._backend.folders()
        if self.folder not in (tab_id for tab_id, _ in self.store.tabs()):
            self.folder = ALL
        self._redraw_chats()

    async def _load_chats(self) -> None:
        seen: set[int] = set()
        async for batch in self._backend.chats():
            self.store.put_chats(batch)
            seen.update(c.id for c in batch)
            self._redraw_chats()
        self.store.forget_chats_except(seen | set(self.open_chats))
        if not self._loaded_once:
            self._say("")  # what said they were loading; later reloads leave notices alone
        self._loaded_once = True
        self._redraw_chats()

    async def _refresh_open_chat(self) -> None:
        """Fetch what was missed; other loaded chats are refreshed when shown."""
        for chat_id, history in self.store.histories.items():
            if chat_id != self.active:
                history.loaded = False
        if self.active is not None:
            history = self.store.history(self.active)
            if history.loaded and history.at_end:
                await self._load_newest(self.active)

    async def _load_newest(self, chat_id: int) -> None:
        chat = self.store.chats.get(chat_id)
        unread = chat.unread if chat else 0
        limit = max(PAGE, min(unread + 20, MAX_UNREAD_PAGE))
        messages = await self._backend.history(chat_id, limit=limit)
        history = self.store.history(chat_id)
        first_time = not history.messages
        history.refresh(messages, complete=len(messages) < limit)
        if chat_id != self.active or self._gone:
            return
        self.conversation.set_note("")
        if first_time and chat:
            self._open_conversation(chat)
        else:
            self.conversation.content_changed()
        self._read_soon()

    def _open_conversation(self, chat: Chat) -> None:
        history = self.store.history(chat.id)
        self.conversation.open(chat, history, self.outbox.waiting(chat.id))
        self._note_place()

    @on(Conversation.NeedNewer)
    def _need_newer(self, event: Conversation.NeedNewer) -> None:
        chat_id = event.chat_id
        if chat_id not in self._loading_newer:
            self._loading_newer.add(chat_id)
            self.run_worker(self._load_newer(chat_id), group=f"newer-{chat_id}")

    @on(Conversation.NeedOlder)
    def _need_older(self, event: Conversation.NeedOlder) -> None:
        chat_id = event.chat_id
        if chat_id not in self._loading_older:
            self._loading_older.add(chat_id)
            self.run_worker(self._load_older(chat_id), group=f"older-{chat_id}")

    async def _load_older(self, chat_id: int) -> None:
        history = self.store.history(chat_id)
        try:
            if history.messages and not history.complete:
                before = history.messages[0].id
                older = await self._backend.history(chat_id, before=before, limit=PAGE)
                if history.messages and history.messages[0].id == before:
                    history.add_older(older, complete=len(older) < PAGE)
                if chat_id == self.active:
                    self.conversation.older_loaded()
        except BackendError as error:
            if chat_id == self.active:
                self._flash(error.message)
        finally:
            self._loading_older.discard(chat_id)

    # -- live updates -------------------------------------------------------

    def _link_changed(self, link: Link) -> None:
        if self._gone:
            return
        self.query_one(StatusBar).link = link
        online = link is Link.ONLINE
        if online and not self._was_online:
            # Telethon does not replay what was missed while disconnected.
            self._load()
            self.outbox.resume()
            self.gallery.try_again()
            self.conversation.refresh()
        self._was_online = online

    def _event(self, event: Event) -> None:
        if self._gone:
            return
        change = self.store.apply(event)
        if self._alerts:
            self._alerts.event(event)
        if isinstance(event, MessagesRemoved):
            self._drafts_lost(event)
        self._show(change)

    def _show(self, change: Change) -> None:
        if change.typing:
            self._show_typing()
        if change.pins or change.stale_pins:
            self._pins_changed(change.pins, change.stale_pins)
        if change.chats:
            self._redraw_chats()
        if self.active is not None:
            chat = self.store.chats.get(self.active)
            if chat:
                self.conversation.update_chat(chat)
            if self.active in change.messages:
                self.conversation.content_changed()
        if change.reload_chats and self._loaded_once:
            self.run_worker(self._reload(self._load_chats), group="reload-chats", exclusive=True)
        if change.reload_folders:
            self.run_worker(self._reload(self._load_folders), group="reload-folders",
                            exclusive=True)  # fmt: skip
        for chat_id in change.stale:
            self.run_worker(self._reload_last(chat_id), group=f"last-{chat_id}", exclusive=True)

    async def _reload(self, what) -> None:
        await asyncio.sleep(0.5)  # several such updates tend to come together
        try:
            await what()
        except BackendError as error:
            log.info("reload failed: %s", error.kind.name)

    async def _reload_last(self, chat_id: int) -> None:
        try:
            messages = await self._backend.history(chat_id, limit=1)
        except BackendError as error:
            log.info("reload failed: %s", error.kind.name)
            return
        self.store.set_last(chat_id, messages[-1] if messages else None)
        self._redraw_chats()

    # -- drawing ------------------------------------------------------------

    def _redraw_chats(self) -> None:
        if self._gone:
            return
        chats = self.store.visible(self.folder)
        self.chat_list.empty_text = "No chats here." if self._loaded_once else "Loading chats..."
        self.chat_list.show(chats, self.active)
        folder_tabs = [
            Tab(tab_id, str(i) if i < 10 else "", title, self.store.unread_chats(tab_id))
            for i, (tab_id, title) in enumerate(self.store.tabs())
        ]
        self.query_one("#folders", TabBar).show(folder_tabs, self.folder)
        tabs = []
        for i, chat_id in enumerate(self.open_chats, 1):
            chat = self.store.chats.get(chat_id)
            if chat:
                badge = chat.unread if chat_id != self.active else 0
                tabs.append(Tab(chat_id, str(i), chat.title, badge))
        self.query_one("#tabs", TabBar).show(tabs, self.active)

    def _arrange(self) -> None:
        """Which panels show, and which has the keys."""
        narrow = self.size.width < NARROW
        hide_list = self.fullscreen or (narrow and self.in_chat)
        sidebar, conversation = self.query_one("#sidebar"), self.query_one("#conversation")
        sidebar.display = not hide_list
        conversation.display = not (narrow and not hide_list)
        sidebar.set_class(narrow, "wide")
        sidebar.set_class(not self.in_chat, "active")
        conversation.set_class(self.in_chat, "active")
        self._show_mode()

    def _show_mode(self) -> None:
        status = self.query_one(StatusBar)
        self.query_one(Divider).light(self.writing)
        if self.writing:
            status.mode, status.hints = "WRITE", _WRITE_HINTS
        else:
            status.mode = ""
            status.hints = hints(self._keys, *(_CHAT_HINTS if self.in_chat else _LIST_HINTS))

    def on_descendant_focus(self) -> None:
        self._focus_moved()

    def on_descendant_blur(self) -> None:
        self._focus_moved()

    def _focus_moved(self) -> None:
        if not self._gone:
            self._show_mode()
            if self.active is not None:
                self._show_header()

    def on_resize(self) -> None:
        self.box.max_rows = max(2, min(MAX_BOX_ROWS, self.size.height // 3))
        self._arrange()

    def _say(self, text: str) -> None:
        """Shown until something else is."""
        if self._notice_timer:
            self._notice_timer.stop()
        if not self._gone:
            self.query_one(StatusBar).notice = text

    def _flash(self, text: str) -> None:
        self._say(text)
        self._notice_timer = self.set_timer(NOTICE_TIME, lambda: self._say(""))

    # -- chats and tabs -----------------------------------------------------

    @on(ChatList.Opened)
    def _chat_clicked(self, event: ChatList.Opened) -> None:
        self.open_chat(event.chat_id)

    @on(TabBar.Chosen, "#folders")
    def _folder_clicked(self, event: TabBar.Chosen) -> None:
        self._choose_folder(event.tab_id)

    @on(TabBar.Chosen, "#tabs")
    def _tab_clicked(self, event: TabBar.Chosen) -> None:
        self.open_chat(event.tab_id)

    def open_chat(self, chat_id: int, load: bool = True) -> None:
        chat = self.store.chats.get(chat_id)
        if chat is None:
            return
        if chat_id not in self.open_chats:
            if len(self.open_chats) >= MAX_TABS:
                self.open_chats.pop(0)
            self.open_chats.append(chat_id)
        self.in_chat = True
        self._arrange()
        if chat_id != self.active:
            self._stop_voice()
            self._leave_window()
            self.active = chat_id
            self._open_conversation(chat)
            self._show_draft()
            self._show_pins()
            self._load_pins(chat_id)
        self.chat_list.select(chat_id)
        self._redraw_chats()
        if not load:
            return
        if not self.store.history(chat_id).loaded:
            self.conversation.set_note("Loading messages...")
            self.run_worker(self._open_history(chat_id), group=f"open-{chat_id}", exclusive=True)
        else:
            self._read_soon()

    def _leave_window(self) -> None:
        """A chat left at old messages is shown from its newest next time."""
        if self.active is not None:
            history = self.store.history(self.active)
            if not history.at_end:
                history.refresh([], complete=False)
                history.loaded = history.complete = False

    async def _open_history(self, chat_id: int) -> None:
        try:
            await self._load_newest(chat_id)
        except BackendError as error:
            if chat_id == self.active:
                key = pretty(self._keys["open"])
                again = f" Press {key} in the list to try again." if key else ""
                self.conversation.set_note(error.message + again)

    def _close_tab(self, chat_id: int) -> None:
        if chat_id not in self.open_chats:
            return
        index = self.open_chats.index(chat_id)
        self.open_chats.remove(chat_id)
        if self.open_chats:
            self.open_chat(self.open_chats[min(index, len(self.open_chats) - 1)])
            return
        self._leave_window()
        self._stop_voice()
        self.active = None
        self._show_pins()
        self.in_chat = self.fullscreen = False
        self.set_focus(None)
        self._show_draft()
        self.conversation.close(self._invitation)
        self._arrange()
        self._redraw_chats()

    def _step_tab(self, step: int) -> None:
        if self.active in self.open_chats:
            index = self.open_chats.index(self.active) + step
            self.open_chat(self.open_chats[index % len(self.open_chats)])

    def _choose_folder(self, folder_id: int) -> None:
        self.folder = folder_id
        self._redraw_chats()
        self.chat_list.go_to_start()

    # -- reading ------------------------------------------------------------

    @on(Conversation.Moved)
    def _moved(self) -> None:
        self._read_soon()

    def _read_soon(self) -> None:
        if self._read_timer:
            self._read_timer.stop()
        self._read_timer = self.set_timer(READ_DELAY, self._read_now)

    def on_screen_resume(self) -> None:
        self._read_soon()

    def _read_now(self) -> None:
        """Mark as read only what is really in front of the user."""
        if not self.app.app_focus or self.app.screen is not self or self.active is None:
            return
        if not self.query_one("#conversation").display:
            return
        chat = self.store.chats.get(self.active)
        seen = self.conversation.last_visible_id()
        if chat and seen > chat.read_inbox and chat.has_unread:
            all_seen = chat.last is not None and seen >= chat.last.id
            mentions = all_seen and chat.mentions > 0
            self.run_worker(self._mark_read(chat.id, seen, mentions), group="read")

    async def _mark_read(self, chat_id: int, up_to: int, mentions: bool) -> None:
        try:
            await self._backend.mark_read(chat_id, up_to, mentions)
        except BackendError as error:
            log.info("mark read failed: %s", error.kind.name)
            return
        self._event(InboxRead(chat_id, up_to))

    # -- keys ---------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        key = pressed(event)
        action = action_for(event, self._keys)
        if len(key) == 1 and key in "0123456789":
            event.stop()
            self._digit(int(key))
            return
        handler = getattr(self, f"do_{action}", None) if action else None
        if handler:
            event.stop()
            event.prevent_default()
            handler()

    def _digit(self, number: int) -> None:
        if self.in_chat:
            if 1 <= number <= len(self.open_chats):
                self.open_chat(self.open_chats[number - 1])
        else:
            tabs = self.store.tabs()
            if number < len(tabs):
                self._choose_folder(tabs[number][0])

    def do_down(self) -> None:
        if self.in_chat:
            self.conversation.move_selection(1)
        else:
            self.chat_list.move(1)

    def do_up(self) -> None:
        if self.in_chat:
            self.conversation.move_selection(-1)
        else:
            self.chat_list.move(-1)

    def do_page_down(self) -> None:
        if self.in_chat:
            self.conversation.scroll_lines(-10)
        else:
            self.chat_list.move(10)

    def do_page_up(self) -> None:
        if self.in_chat:
            self.conversation.scroll_lines(10)
        else:
            self.chat_list.move(-10)

    def do_newest(self) -> None:
        if self.in_chat:
            self.conversation.go_to_newest()
            if self.active is not None and not self.store.history(self.active).at_end:
                self._leave_window()
                work = self._open_history(self.active)
                self.run_worker(work, group=f"open-{self.active}", exclusive=True)
        else:
            self.chat_list.go_to_end()

    def do_focus_list(self) -> None:
        self.set_focus(None)
        self.in_chat = self.fullscreen = False
        self._arrange()

    def do_focus_chat(self) -> None:
        if self.active is not None:
            self.in_chat = True
            self._arrange()
            self._read_soon()

    def do_open(self) -> None:
        if self.in_chat:
            message = self.conversation.selected_message
            if message and message.state is not State.SENT:
                self.outbox.retry(message.chat_id, message.id)
            self.conversation.deselect()
        elif self.chat_list.selected is not None:
            self.open_chat(self.chat_list.selected)

    def do_deselect(self) -> None:
        if not self.conversation.deselect():
            self._cancel_extras()

    def do_next_tab(self) -> None:
        self._step_tab(1)

    def do_previous_tab(self) -> None:
        self._step_tab(-1)

    def do_close_tab(self) -> None:
        if self.active is not None:
            self._close_tab(self.active)

    def do_close_others(self) -> None:
        if self.active is not None:
            self.open_chats = [self.active]
            self._redraw_chats()

    def do_fullscreen(self) -> None:
        if self.active is not None:
            self.fullscreen = not self.fullscreen
            self.in_chat = True
            self._arrange()

    def do_quit(self) -> None:
        unsent = self.outbox.unsent
        drafts = sum(draft.unsent for draft in self._drafts.values())
        loading = len(self._downloads)
        if not unsent and not drafts and not loading:
            self.app.exit()
            return
        parts = []
        if loading:
            parts.append("a download" if loading == 1 else f"{loading} downloads")
        if unsent:
            parts.append(f"{unsent} unsent message" + ("s" if unsent > 1 else ""))
        if drafts:
            parts.append("something you were writing" if drafts == 1 else f"{drafts} drafts")
        self.app.push_screen(
            ConfirmScreen(f"Quit and lose {' and '.join(parts)}?", "quit"),
            lambda yes: self.app.exit() if yes else None,
        )

    def do_logout(self) -> None:
        self.app.push_screen(
            ConfirmScreen(
                "Log out of Telegram on this computer?\n"
                "This also removes the saved login and API keys.",
                "log out",
            ),
            self._log_out_answered,
        )

    def _log_out_answered(self, yes: bool | None) -> None:
        if yes:
            self.run_worker(self._log_out(), group="logout", exclusive=True)

    async def _log_out(self) -> None:
        self._say("Logging out...")
        try:
            await self._backend.log_out()
        except BackendError as error:
            self._say(f"Still logged in. {error.message}")
            return
        self.post_message(self.LoggedOut())
