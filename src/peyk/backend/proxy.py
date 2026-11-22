"""Understand the proxy formats people actually paste."""

from __future__ import annotations

import base64
import binascii
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qs, unquote, urlsplit

Kind = Literal["socks5", "socks4", "http", "mtproxy"]


class ProxyError(ValueError):
    """Message is ready to show to the user and never contains credentials."""


@dataclass(frozen=True, slots=True)
class Proxy:
    kind: Kind
    host: str
    port: int
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    secret: str = field(default="", repr=False)  # MTProxy: 32 hex digits

    def describe(self) -> str:
        name = "MTProxy" if self.kind == "mtproxy" else self.kind.upper()
        return f"{name} {self.host}:{self.port}"


def parse(text: str) -> Proxy | None:
    """A proxy from user input. Empty or "none" means connect directly."""
    text = text.strip()
    if text.lower() in ("", "none"):
        return None
    if text.lower() == "system":
        return system_proxy()
    if "://" not in text:
        is_link = re.match(r"(t|telegram)\.me/", text, re.IGNORECASE)
        text = ("https://" if is_link else "socks5://") + text
    try:
        url = urlsplit(text)
        port = url.port
    except ValueError as error:
        raise ProxyError("That proxy address is not valid.") from error

    scheme = url.scheme.lower()
    if scheme == "tg":
        return _from_link((url.netloc or url.path).strip("/"), url.query)
    if scheme in ("http", "https") and (url.hostname or "") in ("t.me", "telegram.me"):
        return _from_link(url.path.strip("/"), url.query)
    if scheme == "socks5h":
        scheme = "socks5"
    if scheme not in ("socks5", "socks4", "http"):
        raise ProxyError("Use a socks5://, http:// or tg://proxy address.")
    if not url.hostname or port is None:
        raise ProxyError("The proxy needs both a host and a port, like 127.0.0.1:1080.")
    return Proxy(
        scheme,  # type: ignore[arg-type]
        url.hostname,
        _port(port),
        unquote(url.username or ""),
        unquote(url.password or ""),
    )


def system_proxy() -> Proxy | None:
    """The Windows (or environment) proxy, if one is switched on."""
    found = urllib.request.getproxies()
    for name, kind in (("socks", "socks5"), ("https", "http"), ("http", "http"), ("all", "")):
        value = found.get(name)
        if not value:
            continue
        address = value if "://" in value else f"//{value}"
        try:
            url = urlsplit(address)
            port = url.port
        except ValueError:
            continue
        if not url.hostname or not port:
            continue
        if url.scheme.startswith("socks"):
            kind = "socks4" if url.scheme == "socks4" else "socks5"
        return Proxy(
            kind or "http",  # type: ignore[arg-type]
            url.hostname,
            port,
            unquote(url.username or ""),
            unquote(url.password or ""),
        )
    return None


def _from_link(action: str, query: str) -> Proxy:
    # A '+' in a base64 secret must not turn into a space.
    fields = {k: v[0].strip() for k, v in parse_qs(query.replace("+", "%2B")).items()}
    host, port = fields.get("server", ""), fields.get("port", "")
    if not host or not (port.isascii() and port.isdigit()):
        raise ProxyError("That proxy link has no server or port.")
    if action == "socks":
        return Proxy("socks5", host, _port(int(port)), fields.get("user", ""),
                     fields.get("pass", ""))  # fmt: skip
    if action == "proxy":
        return Proxy("mtproxy", host, _port(int(port)), secret=_secret(fields.get("secret", "")))
    raise ProxyError("That link is not a proxy link.")


def _port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise ProxyError("The proxy port must be between 1 and 65535.")
    return port


def _secret(text: str) -> str:
    raw: bytes | None = None
    if re.fullmatch(r"(?:[0-9a-fA-F]{2})+", text):
        raw = bytes.fromhex(text)
    else:
        try:
            padded = text + "=" * (-len(text) % 4)
            raw = base64.b64decode(padded.replace("-", "+").replace("_", "/"), validate=True)
        except (binascii.Error, ValueError):
            raw = None
    if not raw:
        raise ProxyError("That MTProxy secret is not valid.")
    if raw[0] == 0xEE and len(raw) > 16:
        raise ProxyError(
            "This is a FakeTLS MTProxy (its secret starts with 'ee'), which Peyk "
            "cannot use yet. Use a SOCKS5, HTTP or ordinary MTProxy instead."
        )
    if raw[0] == 0xDD and len(raw) == 17:
        raw = raw[1:]
    if len(raw) != 16:
        raise ProxyError("That MTProxy secret has the wrong length.")
    return raw.hex()
