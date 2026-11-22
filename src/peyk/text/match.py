"""Fuzzy matching of names, forgiving about Persian and Arabic spelling variants."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import lru_cache
from unicodedata import category, decimal, normalize

from rapidfuzz import fuzz

_SAME = {
    0x064A: 0x06CC,  # Arabic yeh, alef maksura: Persian yeh
    0x0649: 0x06CC,
    0x0643: 0x06A9,  # Arabic kaf: Persian kaf
    0x0629: 0x0647,  # teh marbuta, heh variants: heh
    0x06D5: 0x0647,
    0x06C1: 0x0647,
    0x0640: None,  # tatweel
}


@lru_cache(maxsize=4096)
def fold(text: str) -> str:
    """Lower case, without accents, joiners or letter variants."""
    out = []
    for char in normalize("NFKD", text).casefold().translate(_SAME):
        kind = category(char)
        if kind in ("Mn", "Me", "Cf"):
            continue
        out.append(str(decimal(char)) if kind == "Nd" else char)
    return " ".join("".join(out).split())


def score(query: str, name: str, username: str = "") -> float | None:
    """How well `name` fits; None if it does not."""
    wanted = fold(query)
    handle = wanted.removeprefix("@")
    if not handle:
        return 0.0
    user = fold(username)
    if user == handle:
        return 99.0
    if user.startswith(handle):
        return 93.0
    if wanted != handle:  # "@..." asks for a username
        return 80.0 if handle in user else None
    target = fold(name)
    if target.startswith(wanted):
        return 100.0
    if f" {wanted}" in f" {target}":
        return 95.0
    if wanted in target:
        return 88.0
    if " " in wanted and all(word in target for word in wanted.split()):
        return 85.0
    if handle in user:
        return 80.0
    spread = _spread(wanted, target)
    if len(wanted) >= 3 and spread is not None and spread <= 2 * len(wanted):
        return max(51.0, 79.0 - spread)
    if len(wanted) >= 4 and len(target) >= len(wanted) - 1:
        near = fuzz.partial_ratio(wanted, target)
        if near >= 75:
            return near / 2
    return None


def _spread(wanted: str, target: str) -> int | None:
    """Length of the stretch holding the query's letters in order."""
    start = target.find(wanted[0])
    if start < 0:
        return None
    position = start
    for char in wanted[1:]:
        position = target.find(char, position + 1)
        if position < 0:
            return None
    return position - start + 1


def ranked[T](query: str, items: Iterable[T], names: Callable[[T], tuple[str, str]]) -> list[T]:
    """Matching items, best first; equally good ones keep their order."""
    found = [(s, item) for item in items if (s := score(query, *names(item))) is not None]
    return [item for _, item in sorted(found, key=lambda pair: -pair[0])]
