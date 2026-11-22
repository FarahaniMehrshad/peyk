"""File logging that never records phone numbers, codes or keys."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Phone numbers, login codes and ids; hashes, keys and proxy secrets.
_DIGITS = re.compile(r"\+?\d(?:[\s()-]?\d){4,}")
_HEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_URL_AUTH = re.compile(r"://[^\s/@]+@")


def redact(text: str) -> str:
    text = _URL_AUTH.sub("://[redacted]@", text)
    text = _HEX.sub("[redacted]", text)
    return _DIGITS.sub("[redacted]", text)


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        body = redact(record.getMessage())
        if record.exc_info and record.exc_info[0]:
            # Tracebacks can quote arguments, so keep only the type and location.
            frames = []
            trace = record.exc_info[2]
            while trace:
                code = trace.tb_frame.f_code
                frames.append(f"{Path(code.co_filename).name}:{trace.tb_lineno}")
                trace = trace.tb_next
            body += f" [{record.exc_info[0].__name__} at {' > '.join(frames[-6:])}]"
        return f"{stamp} {record.levelname} {record.name}: {body}"


def setup(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(_Formatter())
    logging.getLogger("peyk").setLevel(logging.INFO)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    for name in ("peyk", "telethon"):
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        logger.propagate = False
