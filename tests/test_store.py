from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from peyk.backend.base import (
    ChatFlags,
    FoldersChanged,
    InboxRead,
    MessageAdded,
    MessageChanged,
    MessagesRemoved,
    OutboxRead,
    PinsChanged,
)
from peyk.models import FOREVER, Chat, Folder, Kind, Message, Reply
from peyk.store import ALL, ARCHIVE, History, Store

T0 = datetime(2026, 1, 1, tzinfo=UTC)
CHANNEL = -1_000_000_000_005


def msg(i, chat=1, **extra):
    extra.setdefault("sender_id", 9)
    return Message(id=i, chat_id=chat, date=T0 + timedelta(minutes=i), text=f"m{i}", **extra)


def chat(i, last=None, **extra):
    extra.setdefault("kind", Kind.USER)
    return Chat(id=i, title=f"c{i}", last=last, **extra)


def ids(history):
    return [m.id for m in history.messages]


# -- history


def test_history_stays_sorted_and_unique():
    h = History()
    for i in (5, 3, 9, 3, 7):
        h.put(msg(i))
    assert ids(h) == [3, 5, 7, 9]
    assert h.put(msg(4)) and not h.put(msg(4))
    assert h.get(4).id == 4 and h.get(6) is None
    assert h.remove(4).id == 4 and h.remove(4) is None


def test_refresh_merges_when_pages_overlap():
    h = History()
    h.refresh([msg(i) for i in (1, 2, 3, 4)], complete=True)
    h.refresh([msg(i) for i in (3, 4, 5)], complete=False)
    assert ids(h) == [1, 2, 3, 4, 5] and h.complete


def test_refresh_drops_old_messages_rather_than_leave_a_gap():
    h = History()
    h.refresh([msg(i) for i in (1, 2, 3)], complete=True)
    h.refresh([msg(i) for i in (50, 51)], complete=False)
    assert ids(h) == [50, 51] and not h.complete


def test_refresh_notices_deletions():
    h = History()
    h.refresh([msg(i) for i in (1, 2, 3, 4)], complete=True)
    h.refresh([msg(i) for i in (2, 4)], complete=False)
    assert ids(h) == [1, 2, 4]
    h.refresh([], complete=True)
    assert ids(h) == [] and h.loaded


# -- lists and folders


def build(*chats, folders=()):
    store = Store()
    store.put_chats(chats)
    store.folders = list(folders)
    return store


def shown(store, folder=ALL):
    return [c.id for c in store.visible(folder, now=1000)]


def test_order_pins_then_newest():
    store = build(chat(1, msg(1)), chat(2, msg(9)), chat(3, msg(5), pin=2), chat(4, msg(2), pin=1),
                  chat(5))  # fmt: skip
    assert shown(store) == [4, 3, 2, 1, 5]


def test_archive_has_its_own_tab():
    store = build(chat(1, msg(1)), chat(2, msg(2), archived=True))
    assert shown(store) == [1] and shown(store, ARCHIVE) == [2]
    assert store.tabs()[-1] == (ARCHIVE, "Archive")
    assert (ARCHIVE, "Archive") not in build(chat(1)).tabs()


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        (Folder(2, "f", contacts=True), [1, 7]),
        (Folder(2, "f", non_contacts=True), [2]),
        (Folder(2, "f", groups=True), [3]),
        (Folder(2, "f", channels=True), [4]),
        (Folder(2, "f", bots=True), [5]),
        (Folder(2, "f", groups=True, include=frozenset({2})), [2, 3]),
        (Folder(2, "f", groups=True, exclude=frozenset({3})), []),
        (Folder(2, "f", groups=True, include=frozenset({3}), exclude_muted=True), [3]),
        (Folder(2, "f", groups=True, exclude_muted=True), []),
        (Folder(2, "f", non_contacts=True, groups=True, exclude_read=True), [2]),
        (Folder(2, "f", contacts=True, pinned=(5,)), [5, 1, 7]),
        (Folder(2, "f", contacts=True, exclude_archived=True), [7]),
    ],
)
def test_folder_rules(folder, expected):
    store = build(
        chat(1, contact=True, archived=True), chat(2, unread=1),
        chat(3, kind=Kind.GROUP, muted_until=FOREVER), chat(4, kind=Kind.CHANNEL),
        chat(5, kind=Kind.BOT), chat(7, kind=Kind.SAVED), folders=[folder],
    )  # fmt: skip
    assert shown(store, 2) == expected


def test_muted_group_with_a_mention_still_shows():
    store = build(chat(3, kind=Kind.GROUP, muted_until=FOREVER, mentions=1),
                  folders=[Folder(2, "f", groups=True, exclude_muted=True)])  # fmt: skip
    assert shown(store, 2) == [3]


def test_unread_count_skips_muted():
    store = build(chat(1, unread=5), chat(2, unread=1, muted_until=FOREVER),
                  chat(3, marked_unread=True), chat(4))  # fmt: skip
    assert store.unread_chats(ALL, now=1000) == 2


# -- events


def test_new_message_updates_list_and_unread():
    store = build(chat(1, msg(5), read_inbox=5))
    change = store.apply(MessageAdded(msg(6, mentioned=True)))
    c = store.chats[1]
    assert (c.last.id, c.unread, c.mentions, change.chats) == (6, 1, 1, True)
    store.apply(MessageAdded(msg(6)))  # duplicates must not count twice
    assert store.chats[1].unread == 1
    store.apply(MessageAdded(msg(7, outgoing=True)))
    assert store.chats[1].unread == 1 and store.chats[1].last.id == 7


def test_duplicate_into_loaded_history_counts_once():
    store = build(chat(1, msg(5), read_inbox=5))
    store.history(1).refresh([msg(5)], True)
    store.apply(MessageAdded(msg(6)))
    store.apply(MessageAdded(msg(6)))
    assert store.chats[1].unread == 1 and ids(store.history(1)) == [5, 6]


def test_late_older_message_does_not_become_the_preview():
    store = build(chat(1, msg(9), read_inbox=9))
    store.apply(MessageAdded(msg(8)))
    assert store.chats[1].last.id == 9 and store.chats[1].unread == 0


def test_message_is_not_added_to_an_unloaded_history():
    store = build(chat(1, msg(5)))
    store.apply(MessageAdded(msg(6)))
    assert 1 not in store.histories


def test_message_in_unknown_chat():
    store = build()
    assert store.apply(MessageAdded(msg(1, chat=77))).reload_chats
    store.apply(MessageAdded(msg(1, chat=77), chat(77)))
    assert store.chats[77].last.id == 1 and store.chats[77].unread == 1


def test_edit_keeps_the_reply_preview():
    store = build(chat(1, msg(5)))
    store.history(1).refresh([msg(5, reply=Reply(2, "Ann", "hello"))], True)
    change = store.apply(MessageChanged(replace(msg(5), text="new", reply=Reply(2))))
    assert store.history(1).get(5).reply.name == "Ann" and store.chats[1].last.text == "new"
    assert change.messages == {1}


def test_deleting_the_last_message():
    store = build(chat(1, msg(6), unread=1, read_inbox=5))
    store.history(1).refresh([msg(5), msg(6)], True)
    store.apply(MessagesRemoved(None, (6,)))
    assert store.chats[1].last.id == 5 and store.chats[1].unread == 0


def test_deleting_the_last_message_of_an_unloaded_chat_asks_for_it():
    store = build(chat(1, msg(6)))
    assert store.apply(MessagesRemoved(None, (6,))).stale == {1}


def test_private_deletions_never_touch_channels():
    store = build(chat(CHANNEL, msg(6, chat=CHANNEL), kind=Kind.CHANNEL))
    store.history(CHANNEL).refresh([msg(6, chat=CHANNEL)], True)
    change = store.apply(MessagesRemoved(None, (6,)))
    assert ids(store.history(CHANNEL)) == [6] and not change.messages and not change.stale
    store.apply(MessagesRemoved(CHANNEL, (6,)))
    assert ids(store.history(CHANNEL)) == []


def test_reading():
    store = build(chat(1, msg(9), unread=4, mentions=1, marked_unread=True, read_inbox=5))
    store.apply(InboxRead(1, 7, 2))
    assert (store.chats[1].unread, store.chats[1].mentions) == (2, 1)
    store.apply(InboxRead(1, 6, 3))  # stale
    assert store.chats[1].unread == 2
    store.apply(InboxRead(1, 9))
    c = store.chats[1]
    assert (c.unread, c.mentions, c.marked_unread) == (0, 0, False)


def test_partial_read_counts_from_history():
    store = build(chat(1, msg(9), unread=4, read_inbox=5))
    store.history(1).refresh([msg(5), msg(6), msg(7, outgoing=True), msg(8), msg(9)], True)
    store.apply(InboxRead(1, 6))
    assert store.chats[1].unread == 2


def test_outbox_read():
    store = build(chat(1, read_outbox=3))
    assert store.apply(OutboxRead(1, 5)).messages == {1}
    assert store.chats[1].read_outbox == 5
    assert not store.apply(OutboxRead(1, 4)).chats


def test_flags_and_reload_requests():
    store = build(chat(1))
    store.apply(ChatFlags(1, muted_until=FOREVER, archived=True))
    assert store.chats[1].archived and store.chats[1].muted(0)
    store.apply(ChatFlags(1, muted_until=0.0))
    assert not store.chats[1].muted(0) and store.chats[1].archived
    assert store.apply(ChatFlags(99, archived=True)).reload_chats
    assert store.apply(PinsChanged()).reload_chats
    assert store.apply(FoldersChanged()).reload_folders


def test_reload_does_not_undo_live_updates():
    store = build(chat(1, msg(5), unread=0, read_inbox=5))
    store.apply(MessageAdded(msg(6)))
    store.put_chats([chat(1, msg(5), unread=0, read_inbox=5, pin=3)])  # older snapshot
    c = store.chats[1]
    assert (c.last.id, c.unread, c.pin) == (6, 1, 3)


def test_forgetting_left_chats():
    store = build(chat(1), chat(2))
    store.forget_chats_except({2})
    assert list(store.chats) == [2]


# -- looking at old messages


def jumped(*ids_shown):
    history = History()
    history.jump([msg(i) for i in ids_shown])
    return history


def test_jump_replaces_what_was_loaded():
    history = jumped(5, 6, 7)
    assert ids(history) == [5, 6, 7] and history.loaded
    assert not history.at_end and not history.complete
    history.add_newer([msg(8), msg(9)], at_end=True)
    assert ids(history) == [5, 6, 7, 8, 9] and history.at_end
    history.jump([msg(1)])
    history.refresh([msg(8), msg(9)], complete=False)
    assert ids(history) == [8, 9] and history.at_end


def test_new_messages_stay_out_of_an_old_window():
    store = Store()
    store.put_chats([chat(1, last=msg(50))])
    store.history(1).jump([msg(5), msg(6)])
    change = store.apply(MessageAdded(msg(51)))
    assert ids(store.history(1)) == [5, 6] and not change.messages
    assert store.chats[1].last.id == 51 and store.chats[1].unread == 1
    store.apply(MessagesRemoved(1, (51,)))
    assert store.chats[1].last.id == 51  # unknown until fetched
