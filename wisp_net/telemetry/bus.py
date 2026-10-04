"""Telemetry bus with Kafka semantics: named topics, append-only logs, consumer offsets.

In-process and thread-safe. Retention is by record count and by age, like a topic's
`retention.bytes`/`retention.ms`; a consumer that falls behind retention resumes at
the earliest retained offset and is told how many records it lost. A Kafka or
Redpanda client can replace this class behind `publish` / `poll`.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

TOPIC_METRICS = "net.telemetry.metrics.v1"
TOPIC_FLOWS = "net.telemetry.flows.v1"
TOPIC_TOPOLOGY = "net.telemetry.topology.v1"
TOPIC_ALERTS = "net.telemetry.alerts.v1"
TOPIC_EVENTS = "net.telemetry.events.v1"
DEFAULT_TOPICS = (TOPIC_METRICS, TOPIC_FLOWS, TOPIC_TOPOLOGY, TOPIC_ALERTS, TOPIC_EVENTS)


@dataclass(frozen=True)
class Record:
    topic: str
    offset: int
    timestamp: float
    key: str
    value: Any


@dataclass
class _Topic:
    max_records: int
    max_age_s: float
    log: deque[Record] = field(default_factory=deque)
    next_offset: int = 0


@dataclass(frozen=True)
class PollResult:
    records: tuple[Record, ...]
    next_offset: int
    lost: int


class TelemetryBus:
    def __init__(self, max_records: int = 200_000, max_age_s: float = 30 * 86400.0) -> None:
        self._lock = threading.Lock()
        self._defaults = (max_records, max_age_s)
        self._topics: dict[str, _Topic] = {}
        for name in DEFAULT_TOPICS:
            self.create_topic(name)

    def create_topic(self, name: str, max_records: int | None = None, max_age_s: float | None = None) -> None:
        with self._lock:
            if name not in self._topics:
                self._topics[name] = _Topic(max_records or self._defaults[0], max_age_s or self._defaults[1])

    def topics(self) -> list[str]:
        with self._lock:
            return sorted(self._topics)

    def publish(self, topic: str, key: str, value: Any, timestamp: float) -> int:
        with self._lock:
            t = self._topic(topic)
            record = Record(topic, t.next_offset, timestamp, key, value)
            t.log.append(record)
            t.next_offset += 1
            self._expire(t, timestamp)
            return record.offset

    def poll(self, topic: str, offset: int, max_records: int = 10_000) -> PollResult:
        with self._lock:
            t = self._topic(topic)
            earliest = t.log[0].offset if t.log else t.next_offset
            lost = max(0, earliest - offset)
            start = max(offset, earliest)
            skip = start - earliest
            records = [t.log[i] for i in range(skip, min(len(t.log), skip + max_records))]
            next_offset = records[-1].offset + 1 if records else start
            return PollResult(tuple(records), next_offset, lost)

    def end_offset(self, topic: str) -> int:
        with self._lock:
            return self._topic(topic).next_offset

    def _topic(self, name: str) -> _Topic:
        try:
            return self._topics[name]
        except KeyError:
            raise KeyError(f"unknown topic {name!r}") from None

    @staticmethod
    def _expire(t: _Topic, now: float) -> None:
        while len(t.log) > t.max_records:
            t.log.popleft()
        while t.log and now - t.log[0].timestamp > t.max_age_s:
            t.log.popleft()


class Consumer:
    """A consumer group of one: tracks its own offset per topic."""

    def __init__(self, bus: TelemetryBus, topic: str, from_end: bool = False) -> None:
        self.bus = bus
        self.topic = topic
        self.offset = bus.end_offset(topic) if from_end else 0
        self.lost = 0

    def poll(self, max_records: int = 10_000) -> tuple[Record, ...]:
        result = self.bus.poll(self.topic, self.offset, max_records)
        self.offset = result.next_offset
        self.lost += result.lost
        return result.records
