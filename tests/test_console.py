"""The repairs to reading the Windows console."""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)

from ctypes import byref, sizeof, wintypes

from textual.drivers import win32

from peyk.platform.console import _Kernel32

HIGH, LOW = chr(0xD83D), chr(0xDC4D)  # a thumb, in two halves


class FakeKernel:
    def __init__(self, keys):
        self.queue = list(keys)

    def ReadConsoleInputW(self, handle, records, size, count):
        n = min(size, len(self.queue))
        offset = self.offset
        for i in range(n):
            char, down = self.queue.pop(0)
            record = self.array[offset + i]
            record.EventType = 1
            record.Event.KeyEvent.bKeyDown = down
            record.Event.KeyEvent.uChar.UnicodeChar = char
        self.offset += n
        count._obj.value = n
        return 1


def read(keys, size=20):
    array = (win32.INPUT_RECORD * size)()
    fake = FakeKernel(keys)
    fake.array, fake.offset = array, 0
    count = wintypes.DWORD(0)
    _Kernel32(fake).ReadConsoleInputW(None, byref(array), size, byref(count))
    chars = [array[i].Event.KeyEvent.uChar.UnicodeChar for i in range(count.value)
             if array[i].Event.KeyEvent.bKeyDown]  # fmt: skip
    return "".join(chars), len(fake.queue)


def test_an_emoji_at_the_end_of_a_read_is_completed():
    text, left = read([("x", True)] * 11 + [(HIGH, True), (LOW, True), ("y", True)])
    assert text == "x" * 11 + HIGH + LOW and left == 1
    text, left = read([("x", True)] * 10 + [(HIGH, True), (HIGH, False), (LOW, True), ("y", True)])
    assert text == "x" * 10 + HIGH + LOW and left == 1


def test_other_reads_are_untouched():
    text, left = read([("x", True)] * 30)
    assert text == "x" * 12 and left == 18
    assert read([("a", True)])[0] == "a"
    assert sizeof(win32.INPUT_RECORD) == 20
