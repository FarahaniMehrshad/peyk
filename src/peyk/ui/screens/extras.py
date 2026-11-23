"""The part of the main screen for reactions, typing, notifications and maps."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from textual import on
from textual.message import Message as UiMessage

from peyk import config
from peyk.backend.base import BackendError, Link, ReactionsChanged
from peyk.keys import KeyProblem, describe_maps, pretty, resolve_maps
from peyk.models import Chat, Kind, Media, State, VoiceNote
from peyk.platform import notify
from peyk.store import is_channel_id
from peyk.text import format as fmt
from peyk.ui.alerts import Alerts
from peyk.ui.screens.reactions import ReactionPicker
from peyk.ui.screens.stickers import StickerPicker
from peyk.ui.screens.writing import Draft
from peyk.ui.widgets.divider import Divider

if TYPE_CHECKING:
    from peyk.ui.screens.main import MainScreen as _Base
else:
    from textual.screen import Screen as _Base  # a plain mixin would lose its @on handlers

log = logging.getLogger(__name__)
TELL_EVERY = 5.0  # seconds; telling others that we type lasts a little longer
LOOK_EVERY = 20.0  # seconds between asking for reactions, which groups do not always send
MAX_ASKED = 100


class NoticeClicked(UiMessage):
    def __init__(self, chat_id: int) -> None:
        super().__init__()
        self.chat_id = chat_id


class Extras(_Base):
    # -- reactions ----------------------------------------------------------

    def do_react(self) -> None:
        message = self._selected_or_complain("react to")
        if message is None:
            return
        if message.state is not State.SENT:
            self._flash("That message is not sent yet.")
            return
        if message.service:
            self._flash("You cannot react to this.")
            return

        def chosen(keys: tuple[str, ...] | None) -> None:
            if keys is not None:
                work = self._react(message.chat_id, message.id, keys)
                self.run_worker(work, group=f"react-{message.chat_id}-{message.id}")

        self.app.push_screen(
            ReactionPicker(self._backend, self._options, message, self._keys), chosen
        )

    # -- stickers and GIFs ---------------------------------------------------

    def do_stickers(self) -> None:
        self._choose_file(gifs=False)

    def do_gifs(self) -> None:
        self._choose_file(gifs=True)

    def _choose_file(self, gifs: bool) -> None:
        if not self.in_chat and self.chat_list.selected not in (None, self.active):
            self.open_chat(self.chat_list.selected)
        chat = self._chat
        if chat is None:
            self._flash("Open a chat first.")
            return
        if not chat.can_write:
            self._flash("You cannot write in this chat.")
            return
        if self.draft.edit_id:
            self._flash("Finish editing first.")
            return
        self.in_chat = True
        self._arrange()

        def chosen(media: Media | None) -> None:
            if media is not None and not self._gone:
                self._send_file(chat.id, media)

        gallery = self.gallery if self._inline else None
        picker = StickerPicker(
            self._backend, gallery, self._options, self._keys, gifs, self._file_memory
        )
        self.app.push_screen(picker, chosen)

    def _send_file(self, chat_id: int, media: Media | VoiceNote) -> None:
        """A sticker, GIF or voice message goes at once, as a reply if one was being written."""
        draft = self._drafts.setdefault(chat_id, Draft())
        reply, draft.reply = draft.reply, None
        me = self._me.id if self._me else 0
        if isinstance(media, VoiceNote):
            self.outbox.add(chat_id, "", (), reply, me, "", attachment=media)
        else:
            self.outbox.add(chat_id, "", (), reply, me, "", ready=media)
        if chat_id == self.active:
            self._show_draft()
            self.do_newest()

    async def _react(self, chat_id: int, message_id: int, keys: tuple[str, ...]) -> None:
        try:
            reactions = await self._backend.react(chat_id, message_id, keys)
        except BackendError as error:
            self._flash(f"No reaction given. {error.message}")
            return
        self._event(ReactionsChanged(chat_id, message_id, reactions))

    def _look_for_reactions(self) -> None:
        """Only for what is in front of the user."""
        chat_id = self.active
        if chat_id is None or not is_channel_id(chat_id) or not self._in_view(chat_id):
            return
        ids = self.conversation.visible_ids()[-MAX_ASKED:]
        if ids and self._backend.link is Link.ONLINE:
            work = self._refresh_reactions(chat_id, ids)
            self.run_worker(work, group="reactions", exclusive=True)

    async def _refresh_reactions(self, chat_id: int, ids: list[int]) -> None:
        try:
            await self._backend.refresh_reactions(chat_id, ids)
        except BackendError as error:
            log.info("reactions not refreshed: %s", error.kind.name)

    # -- typing -------------------------------------------------------------

    def _tell_typing(self) -> None:
        chat, draft = self._chat, self.draft
        if (
            not self._show_typing_to_others
            or chat is None
            or chat.kind in (Kind.SAVED, Kind.CHANNEL)
            or draft.edit_id
            or not draft.text.strip()
            or self._backend.link is not Link.ONLINE
        ):
            return
        now = time.monotonic()
        if now - self._told_typing.get(chat.id, -TELL_EVERY) >= TELL_EVERY:
            self._told_typing[chat.id] = now
            self.run_worker(self._send_typing(chat.id), group="typing")

    async def _send_typing(self, chat_id: int) -> None:
        try:
            await self._backend.typing(chat_id)
        except BackendError as error:
            log.info("typing not told: %s", error.kind.name)

    def _show_typing(self) -> None:
        doing = {
            chat.id: pieces
            for chat in self.store.chats.values()
            if (pieces := self._doing(chat, short=True))
        }
        self.chat_list.show_doing(doing)
        chat = self._chat
        self.query_one(Divider).show(self._doing(chat) if chat else [])
        if self.store.anyone_typing and self._typing_timer is None:
            self._typing_timer = self.set_timer(1, self._typing_passed)

    def _doing(self, chat: Chat, short: bool = False) -> list[str]:
        typists = self.store.typists(chat.id)
        if not typists:
            return []
        alone = chat.kind in (Kind.USER, Kind.BOT)
        if short and alone:
            return [f"{typists[0].what}…"]
        names = [(t.name.split()[0] if short and t.name else t.name, t.what) for t in typists]
        return fmt.typing(names, chat.title if alone else "")

    def _typing_passed(self) -> None:
        self._typing_timer = None
        self.store.forget_old_typing()
        if not self._gone:
            self._show_typing()

    # -- notifications ------------------------------------------------------

    def _in_view(self, chat_id: int) -> bool:
        return (
            self.app.app_focus
            and chat_id == self.active
            and not self._gone
            and bool(self.query_one("#conversation").display)
        )

    def _focus_changed(self, focused: bool) -> None:
        self._read_soon()
        if focused and self._alerts:
            self._window = notify.terminal_window() or self._window

    # -- notifications on and off

    def _start_alerts(self) -> bool:
        if self._notifier is None:
            return False
        self._notifier.on_click = self._notice_clicked_somewhere
        self._alerts = Alerts(
            self._notifier, self.store, self.set_timer, self._in_view, self._show_content
        )
        return True

    def do_notifications(self, wanted: str = "") -> None:
        """`wanted`: on or off; nothing switches them."""
        on = wanted == "on" if wanted else self._alerts is None
        if on == (self._alerts is not None):
            self._flash(f"Notifications are already {'on' if on else 'off'}.")
            return
        if on:
            if not self._start_alerts():
                self._flash("Notifications cannot be shown here.")
                return
            if self.is_mounted:
                self._window = notify.terminal_window() or self._window
        else:
            self._alerts = None
            if self._notifier:
                self._notifier.on_click = None
                self._notifier.clear(for_good=True)  # and Windows forgets Peyk's name
        state = "on" if on else "off"
        if self._config_path is None:
            self._flash(f"Notifications {state} until Peyk ends.")
            return
        try:
            config.save_setting(self._config_path, "notifications", "enabled", on)
        except config.ConfigError as error:
            self._flash(f"Notifications {state} until Peyk ends: config.toml {error}.")
            return
        self._flash(f"Notifications {state}, also next time.")

    # -- maps: letters that press a key, like vim's :inoremap

    def do_map(self, argument: str = "") -> None:
        """`jj esc` maps, `jj` tells what jj does, nothing lists them all."""
        maps = self._options.maps
        parts = argument.split()
        if not parts:
            listed = describe_maps(maps)
            self._flash(f"Maps: {listed}." if listed else "No maps. :map jj esc makes one.")
            return
        if len(parts) == 1:
            typed = parts[0]
            known = typed in maps
            self._flash(f"{typed} presses {pretty(maps[typed])}." if known
                        else f"{typed} is not mapped.")  # fmt: skip
            return
        if len(parts) > 2:
            self._flash("map takes the letters, then a key: :map jj esc")
            return
        typed, written = parts
        try:
            wanted = resolve_maps({**maps, typed: written})
        except KeyProblem as problem:
            self._flash(f"Not mapped: {problem}.")
            return
        maps[typed] = wanted[typed]
        self._keep_map(typed, wanted[typed], f"{typed} presses {pretty(wanted[typed])}")

    def do_unmap(self, argument: str = "") -> None:
        typed = argument.strip()
        if not typed:
            self._flash("unmap takes the letters: :unmap jj")
        elif typed not in self._options.maps:
            self._flash(f"{typed} is not mapped.")
        else:
            del self._options.maps[typed]
            self._keep_map(typed, None, f"{typed} are letters again")

    def _keep_map(self, typed: str, key: str | None, done: str) -> None:
        if self._config_path is None:
            self._flash(f"{done} until Peyk ends.")
            return
        try:
            config.save_map(self._config_path, typed, key)
        except config.ConfigError as error:
            self._flash(f"{done} until Peyk ends: config.toml {error}.")
            return
        self._flash(f"{done}, also next time.")

    def _notice_clicked_somewhere(self, chat_id: int) -> None:
        """From a thread of Windows."""
        self.post_message(NoticeClicked(chat_id))

    @on(NoticeClicked)
    def _notice_clicked(self, event: NoticeClicked) -> None:
        notify.bring_to_front(self._window)
        if event.chat_id in self.store.chats:
            self.set_focus(None)  # do not write into another chat by accident
            self.open_chat(event.chat_id)
