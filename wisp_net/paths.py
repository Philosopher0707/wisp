"""gNMI-style paths: `/interfaces/interface[name=Ethernet1]/state/oper-status`.

Parsing is bracket-aware because key values may themselves contain `/`
(`ipv4-entry[prefix=10.1.0.0/24]`). A pattern may use `*` for an element name or a
key value, and matches every path it is a prefix of, as a gNMI Get/Subscribe does.
"""

from __future__ import annotations

from dataclasses import dataclass


class PathError(ValueError):
    pass


@dataclass(frozen=True)
class PathElem:
    name: str
    keys: tuple[tuple[str, str], ...] = ()

    def key(self, name: str) -> str | None:
        for k, v in self.keys:
            if k == name:
                return v
        return None

    def __str__(self) -> str:
        return self.name + "".join(f"[{k}={v}]" for k, v in self.keys)


Path = tuple[PathElem, ...]


def parse_path(text: str) -> Path:
    text = text.strip()
    if not text.startswith("/"):
        raise PathError(f"path must start with '/': {text!r}")
    elems: list[PathElem] = []
    i, n = 1, len(text)
    while i < n:
        name_start = i
        while i < n and text[i] not in "/[":
            i += 1
        name = text[name_start:i]
        if not name:
            raise PathError(f"empty element in {text!r}")
        keys: list[tuple[str, str]] = []
        while i < n and text[i] == "[":
            close = _find_close(text, i)
            body = text[i + 1:close]
            if "=" not in body:
                raise PathError(f"key without '=' in {text!r}")
            k, v = body.split("=", 1)
            if not k:
                raise PathError(f"empty key name in {text!r}")
            keys.append((k, v))
            i = close + 1
        if i < n and text[i] != "/":
            raise PathError(f"unexpected {text[i]!r} in {text!r}")
        i += 1
        elems.append(PathElem(name, tuple(sorted(keys))))
    return tuple(elems)


def _find_close(text: str, open_at: int) -> int:
    close = text.find("]", open_at)
    if close < 0:
        raise PathError(f"unclosed '[' in {text!r}")
    return close


def format_path(path: Path) -> str:
    return "/" + "/".join(str(e) for e in path)


def matches(pattern: Path, path: Path) -> bool:
    """True when `pattern` addresses `path` or a subtree containing it."""
    if len(pattern) > len(path):
        return False
    for p, e in zip(pattern, path):
        if p.name != "*" and p.name != e.name:
            return False
        for k, v in p.keys:
            actual = e.key(k)
            if actual is None or (v != "*" and v != actual):
                return False
    return True


def leaf_name(path: Path) -> str:
    return path[-1].name if path else ""
