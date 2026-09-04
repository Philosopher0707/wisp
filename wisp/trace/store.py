"""Trace span store (M5): append + query over UnifiedStore.

Redaction is applied at append() — a misconfigured reader/exporter cannot
leak secrets that were never persisted. Query paths are read-only views
over (trace_id) or (run_id attr) indexes.
"""
from __future__ import annotations
import abc
from typing import Any

from wisp.trace.span import Span


class TraceStore(abc.ABC):
    @abc.abstractmethod
    def append(self, span: Span) -> None: ...
    @abc.abstractmethod
    def query(self, trace_id: str) -> list[Span]: ...
    @abc.abstractmethod
    def query_run(self, run_id: str) -> list[Span]: ...


class SQLiteTraceStore(TraceStore):
    def __init__(self, store: Any):
        self._store = store

    def append(self, span: Span) -> None:
        from wisp.auth.secrets import redact_record
        clean = Span.from_dict({**span.to_dict(),
                                "attrs": redact_record(span.attrs)})
        row = clean.to_dict()
        row["run_id"] = str(clean.attrs.get("run_id", ""))
        self._store.trace_append(row)

    def query(self, trace_id: str) -> list[Span]:
        return [Span.from_dict(r) for r in self._store.trace_list(trace_id)]

    def query_run(self, run_id: str) -> list[Span]:
        return [Span.from_dict(r) for r in self._store.trace_list_run(run_id)]
