"""Telethon session that keeps nothing readable on disk.

Telethon's own SQLite session stores the login key in the clear, plus the name,
username and phone number of everyone it has seen. Here the key is encrypted
and people are stored only as the numeric id and access hash the API needs.
"""

from __future__ import annotations

from pathlib import Path

from telethon.crypto import AuthKey
from telethon.sessions import SQLiteSession

from peyk.secrets import SecretError, protect, unprotect


class SessionUnreadable(Exception):
    """The session file belongs to another Windows user or is damaged."""


class EncryptedSession(SQLiteSession):
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            super().__init__(str(path))
            row = self._execute("select auth_key from sessions")
            key = unprotect(row[0]) if row and row[0] else b""
        except SecretError as error:
            self.close()
            raise SessionUnreadable from error
        except Exception as error:  # sqlite3.DatabaseError, unexpected schema
            if getattr(self, "_conn", None) is not None:
                self._conn.close()
                self._conn = None
            raise SessionUnreadable from error
        self._auth_key = AuthKey(data=key) if key else None

    def set_dc(self, dc_id, server_address, port):
        # The base class re-reads the key column, which holds ciphertext here.
        key, temporary = self._auth_key, self._tmp_auth_key
        super().set_dc(dc_id, server_address, port)
        self._auth_key, self._tmp_auth_key = key, temporary

    def _update_session_table(self):
        sealed = protect(self._auth_key.key) if self._auth_key and self._auth_key.key else b""
        cursor = self._cursor()
        cursor.execute("delete from sessions")
        cursor.execute(
            "insert into sessions values (?,?,?,?,?,?)",
            (self._dc_id, self._server_address, self._port, sealed, self._takeout_id, b""),
        )
        cursor.close()

    @staticmethod
    def _entity_values_to_row(id, hash, username, phone, name):  # noqa: A002
        return id, hash, None, None, None


def remove_session(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
