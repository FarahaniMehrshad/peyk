"""Event loop settings."""

from __future__ import annotations

import asyncio


def use_lazy_tasks() -> None:
    """Undo Textual's eager task factory on the running loop.

    Textual makes tasks start running inside create_task(). Telethon creates its
    send and receive loops just before setting the flag those loops check, so
    when started eagerly they exit at once and every request waits forever.
    Textual's test harness does not enable eager tasks, so tests cannot see this
    unless they set the factory themselves.
    """
    loop = asyncio.get_running_loop()
    if loop.get_task_factory() is not None:
        loop.set_task_factory(None)
