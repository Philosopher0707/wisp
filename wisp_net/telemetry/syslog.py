"""RFC 5424 syslog: parsing, normalization into network events, duplicate suppression."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SEVERITY_NAMES = ("emergency", "alert", "critical", "error", "warning", "notice", "info", "debug")
NIL = "-"


class SyslogParseError(ValueError):
    pass


@dataclass(frozen=True)
class SyslogMessage:
    facility: int
    severity: int
    version: int
    timestamp: float | None
    hostname: str | None
    app_name: str | None
    procid: str | None
    msgid: str | None
    structured_data: dict[str, dict[str, str]]
    msg: str

    @property
    def severity_name(self) -> str:
        return SEVERITY_NAMES[self.severity]


_HEADER = re.compile(r"^<(\d{1,3})>(\d{1,2}) (\S+) (\S+) (\S+) (\S+) (\S+) ")


def parse(line: str) -> SyslogMessage:
    """Parse one RFC 5424 message (header, structured data, optional MSG)."""
    m = _HEADER.match(line)
    if not m:
        raise SyslogParseError(f"not an RFC 5424 header: {line[:80]!r}")
    pri = int(m.group(1))
    if pri > 191:
        raise SyslogParseError(f"PRI out of range: {pri}")
    ts_raw, host, app, procid, msgid = m.group(3, 4, 5, 6, 7)
    sd, rest = _parse_structured_data(line, m.end())
    msg = rest[1:] if rest.startswith(" ") else rest
    if msg.startswith("﻿"):
        msg = msg[1:]
    return SyslogMessage(
        facility=pri // 8, severity=pri % 8, version=int(m.group(2)),
        timestamp=None if ts_raw == NIL else _parse_timestamp(ts_raw),
        hostname=_nil(host), app_name=_nil(app), procid=_nil(procid), msgid=_nil(msgid),
        structured_data=sd, msg=msg)


def _nil(value: str) -> str | None:
    return None if value == NIL else value


def _parse_timestamp(raw: str) -> float:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise SyslogParseError(f"bad TIMESTAMP {raw!r}") from exc


def _parse_structured_data(line: str, i: int) -> tuple[dict[str, dict[str, str]], str]:
    if line.startswith(NIL, i):
        return {}, line[i + 1:]
    elements: dict[str, dict[str, str]] = {}
    n = len(line)
    while i < n and line[i] == "[":
        i += 1
        start = i
        while i < n and line[i] not in " ]":
            i += 1
        sd_id = line[start:i]
        params: dict[str, str] = {}
        while i < n and line[i] == " ":
            i += 1
            start = i
            while i < n and line[i] != "=":
                i += 1
            name = line[start:i]
            if i + 1 >= n or line[i + 1] != '"':
                raise SyslogParseError(f"SD-PARAM {name!r} value is not quoted")
            i += 2
            value: list[str] = []
            while i < n and line[i] != '"':
                if line[i] == "\\" and i + 1 < n and line[i + 1] in '"\\]':
                    i += 1
                value.append(line[i])
                i += 1
            if i >= n:
                raise SyslogParseError("unterminated SD-PARAM value")
            i += 1
            params[name] = "".join(value)
        if i >= n or line[i] != "]":
            raise SyslogParseError(f"unterminated SD-ELEMENT {sd_id!r}")
        i += 1
        elements[sd_id] = params
    if not elements:
        raise SyslogParseError("STRUCTURED-DATA is neither '-' nor an SD-ELEMENT")
    return elements, line[i:]


@dataclass(frozen=True)
class NetEvent:
    timestamp: float
    device: str
    kind: str
    severity: str
    subject: str
    attrs: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    repeats: int = 1


_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("interface_down", re.compile(r"Interface (?P<subject>\S+) changed state to down"), "LINK_DOWN"),
    ("interface_up", re.compile(r"Interface (?P<subject>\S+) changed state to up"), "LINK_UP"),
    ("bgp_session_change", re.compile(
        r"BGP neighbor (?P<subject>\S+) \(AS (?P<peer_as>\d+)\) changed state from (?P<old>\w+) to (?P<new>\w+)"),
     "BGP_ADJCHANGE"),
    ("fcs_errors", re.compile(r"Interface (?P<subject>\S+): (?P<count>\d+) FCS errors"), "FCS_ERRORS"),
    ("rx_power_low", re.compile(r"Rx power low alarm on (?P<subject>\S+): (?P<dbm>-?[\d.]+) dBm"), "RX_POWER_LOW"),
    ("config_commit", re.compile(r"Configuration committed by (?P<subject>\S+)"), "CONFIG_COMMIT"),
    ("auth_failure", re.compile(r"authentication failure .*user=(?P<subject>\S+)"), "AUTH_FAIL"),
)


def normalize(message: SyslogMessage) -> NetEvent:
    """Map a parsed message onto a typed network event; unknown messages stay `unclassified`."""
    device = message.hostname or "unknown"
    ts = message.timestamp or 0.0
    for kind, pattern, msgid in _RULES:
        if message.msgid not in (None, msgid):
            continue
        m = pattern.search(message.msg)
        if m:
            attrs: dict[str, Any] = {k: v for k, v in m.groupdict().items() if k != "subject"}
            for sd in message.structured_data.values():
                attrs.update(sd)
            if kind == "bgp_session_change":
                attrs["established"] = attrs["new"].lower() == "established"
            return NetEvent(ts, device, kind, message.severity_name, m.group("subject"), attrs, message.msg)
    return NetEvent(ts, device, "unclassified", message.severity_name, message.app_name or "",
                    {}, message.msg)


class Deduplicator:
    """Collapse repeats of the same (device, kind, subject, state) inside a window."""

    def __init__(self, window_s: float = 30.0) -> None:
        self.window_s = window_s
        self._last: dict[tuple[str, str, str, str], tuple[float, int]] = {}
        self.suppressed = 0

    def admit(self, event: NetEvent) -> NetEvent | None:
        key = (event.device, event.kind, event.subject, str(event.attrs.get("new", "")))
        seen = self._last.get(key)
        if seen is not None and event.timestamp - seen[0] < self.window_s:
            self._last[key] = (seen[0], seen[1] + 1)
            self.suppressed += 1
            return None
        self._last[key] = (event.timestamp, 1)
        return event
