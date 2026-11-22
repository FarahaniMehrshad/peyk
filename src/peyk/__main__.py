"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import replace

from peyk import __version__
from peyk import config as configuration
from peyk.instance import AlreadyRunning, InstanceLock
from peyk.paths import Paths


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="peyk", description="Telegram in your terminal.")
    parser.add_argument("--version", action="version", version=f"peyk {__version__}")
    parser.add_argument(
        "--demo",
        nargs="?",
        const="main",
        choices=("main", "login"),
        help="run offline on made-up data; 'login' starts at the sign-in screen",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    paths = Paths.default()
    demo = arguments.demo is not None
    if not demo and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("peyk: needs an interactive terminal", file=sys.stderr)
        return 2
    try:
        config = configuration.load(paths.config, create=not demo)
    except configuration.ConfigError as error:
        print(f"peyk: {paths.config}: {error}", file=sys.stderr)
        return 2

    # Esc followed quickly by another key would be taken for Alt and that key.
    os.environ.setdefault("ESCDELAY", "25")
    # Imported late so --help and config errors stay fast.
    from peyk.platform import graphics, palette
    from peyk.platform.console import fix_input
    from peyk.ui.app import PeykApp

    pictures = graphics.detect(config.images)
    colours = palette.query() if config.theme == "terminal" else None
    fix_input()
    if demo:
        from peyk.backend.fake import FakeBackend

        fake = FakeBackend(signed_in=arguments.demo == "main", chatter=8.0)
        with tempfile.TemporaryDirectory(prefix="peyk-demo-", ignore_cleanup_errors=True) as folder:
            config = replace(config, folder=folder)
            PeykApp(config, None, backend=fake, graphics=pictures, palette=colours).run()
        return 0

    from peyk import logs
    from peyk.backend.telegram import TelegramBackend

    lock = InstanceLock(paths.lock)
    try:
        lock.acquire()
    except AlreadyRunning:
        print("peyk: already running in another window", file=sys.stderr)
        return 1
    try:
        logs.setup(paths.log)
        PeykApp(
            config,
            paths,
            factory=lambda secrets, proxy: TelegramBackend(
                paths.session, secrets.api_id, secrets.api_hash, proxy
            ),
            graphics=pictures,
            palette=colours,
        ).run()
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
