"""Encrypt small secrets with Windows DPAPI.

DPAPI ties the ciphertext to the current Windows user, so a copied file is
useless to another account or machine. It does not protect against programs
running as the same user.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from pathlib import Path

_ENTROPY = b"peyk/v1"
_UI_FORBIDDEN = 0x1


class SecretError(Exception):
    """Data could not be encrypted or decrypted."""


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    @classmethod
    def of(cls, data: bytes) -> _Blob:
        buffer = ctypes.create_string_buffer(data, len(data))
        return cls(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _crypt(function: str, data: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecretError("secret storage needs Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    call = getattr(crypt32, function)
    blob = ctypes.POINTER(_Blob)
    call.argtypes = [blob, ctypes.c_void_p, blob, ctypes.c_void_p, ctypes.c_void_p,
                     wintypes.DWORD, blob]  # fmt: skip
    call.restype = wintypes.BOOL
    source, entropy, out = _Blob.of(data), _Blob.of(_ENTROPY), _Blob()
    ok = call(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None, _UI_FORBIDDEN,
        ctypes.byref(out),
    )  # fmt: skip
    if not ok:
        raise SecretError(f"Windows refused ({function}, error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        kernel32.LocalFree(out.data)


def protect(data: bytes) -> bytes:
    return _crypt("CryptProtectData", data)


def unprotect(data: bytes) -> bytes:
    return _crypt("CryptUnprotectData", data)


@dataclass(frozen=True, slots=True)
class Secrets:
    api_id: int
    api_hash: str = field(repr=False)
    proxy: str = field(default="", repr=False)


def load(path: Path) -> Secrets | None:
    """Stored secrets, or None if there are none yet."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return Secrets(**json.loads(unprotect(raw)))
    except (ValueError, TypeError) as error:
        raise SecretError("the stored API keys are damaged") from error


def save(path: Path, secrets: Secrets) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(protect(json.dumps(asdict(secrets)).encode()))
    os.replace(temporary, path)


def delete(path: Path) -> None:
    path.unlink(missing_ok=True)
