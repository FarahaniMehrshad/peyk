"""Config, secrets, session file, instance lock and logs."""

import logging
import sqlite3
import subprocess
import sys

import pytest
from telethon.crypto import AuthKey
from telethon.tl import types

from peyk import config, logs, secrets
from peyk.backend.session import EncryptedSession, SessionUnreadable, remove_session
from peyk.instance import InstanceLock

KEY = bytes(range(256))


# -- config


def test_template_is_the_defaults():
    assert config.parse(config.TEMPLATE) == config.Config()


def test_first_load_writes_the_template(home):
    assert config.load(home.config) == config.Config()
    assert home.config.read_text(encoding="utf-8") == config.TEMPLATE
    assert config.load(home.config) == config.Config()


def test_demo_load_writes_nothing(home):
    config.load(home.config, create=False)
    assert not home.config.exists()


def test_values_are_read():
    parsed = config.parse('[text]\nrtl = "off"\n[notifications]\nenabled = false\n[keys]\nquit="Q"')
    assert parsed.rtl == "off" and not parsed.notifications and parsed.keys == {"quit": "Q"}


def test_notepad_byte_order_mark_is_accepted(home):
    home.config.parent.mkdir(parents=True)
    home.config.write_text('[text]\nrtl = "off"\n', encoding="utf-8-sig")
    assert config.load(home.config).rtl == "off"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[text", "not valid TOML"),
        ('[text]\nrtl = "sideways"', "must be one of"),
        ("[notifications]\nenabled = 1", "true or false"),
        ("[network]\nproxy = 5", "a string"),
        ('[text]\ntypo = "x"', "unknown setting [text] typo"),
        ("loose = 1", "inside a section"),
        ("[keys]\nquit = 1", "must be a string"),
    ],
)
def test_bad_config_is_explained(text, expected):
    with pytest.raises(config.ConfigError, match=expected.replace("[", r"\[")):
        config.parse(text)


# -- secrets


def test_secrets_round_trip_without_plain_text(home):
    original = secrets.Secrets(1234567, "0123456789abcdef" * 2, "socks5://user:hunter2@h:1")
    secrets.save(home.secrets, original)
    raw = home.secrets.read_bytes()
    assert b"hunter2" not in raw and b"0123456789abcdef" not in raw and b"1234567" not in raw
    assert secrets.load(home.secrets) == original
    assert [p.name for p in home.data_dir.iterdir()] == ["secrets.bin"]


def test_secrets_do_not_show_in_repr():
    text = repr(secrets.Secrets(1, "deadbeef", "socks5://u:p@h:1"))
    assert "deadbeef" not in text and "u:p" not in text


def test_missing_and_damaged_secrets(home):
    assert secrets.load(home.secrets) is None
    home.secrets.parent.mkdir(parents=True)
    home.secrets.write_bytes(b"garbage")
    with pytest.raises(secrets.SecretError):
        secrets.load(home.secrets)
    home.secrets.write_bytes(secrets.protect(b'{"unexpected": 1}'))
    with pytest.raises(secrets.SecretError):
        secrets.load(home.secrets)


# -- session


def all_bytes(directory):
    return b"".join(p.read_bytes() for p in directory.iterdir() if p.is_file())


def test_login_key_never_reaches_the_disk(home):
    session = EncryptedSession(home.session)
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(data=KEY)
    session.tmp_auth_key = AuthKey(data=KEY[::-1])
    # Look while a transaction is open too: that is when a journal exists.
    assert KEY not in all_bytes(home.data_dir) and KEY[::-1] not in all_bytes(home.data_dir)
    session.save()
    session.set_dc(4, "149.154.167.91", 443)
    assert session.auth_key.key == KEY
    session.save()
    session.close()
    data = all_bytes(home.data_dir)
    assert KEY not in data and KEY[::-1] not in data and KEY[16:48] not in data

    reopened = EncryptedSession(home.session)
    assert reopened.auth_key.key == KEY
    assert (reopened.dc_id, reopened.server_address) == (4, "149.154.167.91")
    reopened.close()


def test_clearing_the_key_is_persistent(home):
    session = EncryptedSession(home.session)
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(data=KEY)
    session.auth_key = None
    session.close()
    reopened = EncryptedSession(home.session)
    assert not reopened.auth_key
    reopened.close()


def test_people_are_stored_without_personal_details(home):
    session = EncryptedSession(home.session)
    session.process_entities([
        types.User(id=42, access_hash=99, first_name="Zeynab", last_name="Secretname",
                   username="hiddenhandle", phone="447700900123"),
        types.Channel(id=7, title="Privatechannel", photo=types.ChatPhotoEmpty(), date=None,
                      access_hash=5, username="chanhandle"),
    ])  # fmt: skip
    session.close()
    data = all_bytes(home.data_dir).lower()
    for text in (b"zeynab", b"secretname", b"hiddenhandle", b"447700900123", b"privatechannel",
                 b"chanhandle"):  # fmt: skip
        assert text not in data
    rows = sqlite3.connect(home.session).execute("select id, hash, username, phone, name from "
                                                 "entities order by id").fetchall()  # fmt: skip
    assert (42, 99, None, None, None) in rows and len(rows) == 2
    reopened = EncryptedSession(home.session)
    assert reopened.get_input_entity(42).access_hash == 99
    reopened.close()


def test_session_from_another_account_or_damaged(home):
    EncryptedSession(home.session).close()
    with sqlite3.connect(home.session) as database:
        database.execute("delete from sessions")
        database.execute("insert into sessions values (2,'x',443,?,null,'')", (b"not ours" * 30,))
    database.close()
    with pytest.raises(SessionUnreadable):
        EncryptedSession(home.session)
    home.session.write_bytes(b"this is not a database at all" * 100)
    with pytest.raises(SessionUnreadable):
        EncryptedSession(home.session)
    remove_session(home.session)  # must not be held open on Windows
    assert not home.session.exists()


# -- one instance


def test_second_instance_is_refused(home):
    lock = InstanceLock(home.lock)
    lock.acquire()
    code = (
        "import sys;from pathlib import Path;from peyk.instance import *\n"
        "try: InstanceLock(Path(sys.argv[1])).acquire()\n"
        "except AlreadyRunning: sys.exit(7)"
    )
    command = [sys.executable, "-c", code, str(home.lock)]
    assert subprocess.run(command).returncode == 7
    lock.release()
    assert subprocess.run(command).returncode == 0


# -- logs


def test_logs_are_redacted(home):
    logs.setup(home.log)
    logger = logging.getLogger("peyk.test")
    logger.info("phone +44 7700 900123 code 12345 hash %s", "0123456789abcdef" * 2)
    logger.info("proxy socks5://user:hunter2@host:1080")
    try:
        raise ValueError("secret 447700900123 in an exception")
    except ValueError:
        logger.warning("failed", exc_info=True)
    for handler in logging.getLogger("peyk").handlers:
        handler.close()
    for name in ("peyk", "telethon"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    text = home.log.read_text(encoding="utf-8")
    body = "".join(line[20:] for line in text.splitlines(keepends=True))
    for secret in ("7700", "900123", "12345", "0123456789abcdef", "hunter2", "in an exception"):
        assert secret not in body
    assert "ValueError at test_storage.py:" in body


def test_formatting_can_be_turned_off():
    assert config.parse("").formatting
    assert not config.parse("[text]\nformatting = false").formatting
