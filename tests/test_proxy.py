import base64
import urllib.request

import pytest

from peyk.backend import proxy
from peyk.backend.proxy import Proxy, ProxyError, parse

SECRET = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("text", ["", "  ", "none", "NONE"])
def test_direct(text):
    assert parse(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("127.0.0.1:1080", Proxy("socks5", "127.0.0.1", 1080)),
        ("localhost:9050", Proxy("socks5", "localhost", 9050)),
        ("socks5://u:p%40ss@host:1", Proxy("socks5", "host", 1, "u", "p@ss")),
        ("socks5h://host:1", Proxy("socks5", "host", 1)),
        ("SOCKS4://host:1", Proxy("socks4", "host", 1)),
        ("http://10.0.0.1:8080", Proxy("http", "10.0.0.1", 8080)),
        ("socks5://[::1]:1080", Proxy("socks5", "::1", 1080)),
        (
            "tg://socks?server=h&port=2&user=a&pass=b",
            Proxy("socks5", "h", 2, "a", "b"),
        ),
        ("t.me/socks?server=h&port=2", Proxy("socks5", "h", 2)),
        (
            f"tg://proxy?server=h&port=443&secret={SECRET}",
            Proxy("mtproxy", "h", 443, secret=SECRET),
        ),
        (
            f"https://t.me/proxy?server=h&port=443&secret=dd{SECRET}",
            Proxy("mtproxy", "h", 443, secret=SECRET),
        ),
    ],
)
def test_formats(text, expected):
    assert parse(text) == expected


def test_base64_secrets_including_plus():
    raw = bytes([0xFB, 0xEF, 0xBE]) + bytes(13)
    for encoded in (base64.b64encode(raw), base64.urlsafe_b64encode(raw).rstrip(b"=")):
        found = parse(f"tg://proxy?server=h&port=1&secret={encoded.decode()}")
        assert found.secret == raw.hex()


def test_plain_secret_starting_with_dd_or_ee_is_kept():
    for start in ("dd", "ee"):
        secret = start + "0" * 30
        assert parse(f"tg://proxy?server=h&port=1&secret={secret}").secret == secret


@pytest.mark.parametrize(
    "secret",
    ["ee" + SECRET + b"example.com".hex(), base64.b64encode(b"\xee" + bytes(16) + b"a.b").decode()],
)
def test_faketls_is_refused_clearly(secret):
    with pytest.raises(ProxyError, match="FakeTLS"):
        parse(f"tg://proxy?server=h&port=1&secret={secret}")


@pytest.mark.parametrize(
    "text",
    [
        "socks5://host",
        "socks5://:1080",
        "host:0",
        "host:99999",
        "host:abc",
        "ftp://host:21",
        "https://host:443",
        "tg://proxy?server=h",
        "tg://resolve?domain=x",
        "tg://proxy?server=h&port=1&secret=zz",
        "tg://proxy?server=h&port=1&secret=",
        f"tg://proxy?server=h&port=0&secret={SECRET}",
        "tg://proxy?server=h&port=1&secret=abcd",
    ],
)
def test_rejects(text):
    with pytest.raises(ProxyError):
        parse(text)


def test_credentials_stay_out_of_repr_and_messages():
    found = parse("socks5://user:hunter2@host:1")
    assert "hunter2" not in repr(found) and "user" not in repr(found)
    assert found.describe() == "SOCKS5 host:1"
    with pytest.raises(ProxyError) as error:
        parse("socks5://user:hunter2@host")
    assert "hunter2" not in str(error.value)


@pytest.mark.parametrize(
    ("found", "expected"),
    [
        ({}, None),
        ({"https": "https://127.0.0.1:10809"}, Proxy("http", "127.0.0.1", 10809)),
        ({"http": "127.0.0.1:8080"}, Proxy("http", "127.0.0.1", 8080)),
        (
            {"socks": "socks://127.0.0.1:1080", "http": "http://x:1"},
            Proxy("socks5", "127.0.0.1", 1080),
        ),
        ({"all": "socks5://h:5"}, Proxy("socks5", "h", 5)),
        ({"http": "nonsense"}, None),
    ],
)
def test_system_proxy(monkeypatch, found, expected):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: found)
    assert proxy.parse("system") == expected
