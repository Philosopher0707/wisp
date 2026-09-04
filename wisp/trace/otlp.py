"""Tier-gated OTLP/HTTP-JSON span exporter (M5, stdlib only).

Data tiers (ADR telemetry-privacy): metrics-only → metadata →
redacted-content → local-only-full (refuses export). No payload above
the configured tier ever leaves the machine. Transport errors report a
zero count; they never raise (observability must not break turns).
"""
from __future__ import annotations
import json
import urllib.request
from enum import StrEnum
from typing import Any

from wisp.trace.span import Span


class DataTier(StrEnum):
    METRICS_ONLY = "metrics-only"
    METADATA = "metadata"
    REDACTED_CONTENT = "redacted-content"
    LOCAL_ONLY_FULL = "local-only-full"


class ExportRefused(Exception):
    """Raised when the tier forbids export entirely."""


def _project(span: Span, tier: DataTier) -> dict[str, Any]:
    base: dict[str, Any] = {
        "trace_id": span.trace_id, "span_id": span.span_id,
        "parent_span_id": span.parent_span_id, "kind": span.kind,
        "name": span.name, "status": span.status.value,
        "duration_ms": span.duration_ms,
    }
    if tier in (DataTier.METADATA, DataTier.REDACTED_CONTENT):
        base["attrs"] = dict(span.attrs) if tier == DataTier.REDACTED_CONTENT else {}
    else:
        base["attrs"] = {}
    return base


def export_spans(spans: list[Span], endpoint: str, tier: DataTier,
                 timeout_s: float = 5.0) -> int:
    """POST spans as OTLP/HTTP-JSON. Returns spans accepted (0 on error).

    Spans are redacted at store-append; REDACTED_CONTENT additionally
    re-scrubs before send (defense in depth for hand-built spans).
    """
    if tier == DataTier.LOCAL_ONLY_FULL:
        raise ExportRefused("local-only-full spans must never leave the machine")
    if tier == DataTier.REDACTED_CONTENT:
        from wisp.auth.secrets import redact_record
        spans = [Span.from_dict({**s.to_dict(),
                                 "attrs": redact_record(s.attrs)}) for s in spans]
    body = json.dumps({"spans": [_project(s, tier) for s in spans]}).encode()
    req = urllib.request.Request(
        endpoint, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return len(spans) if 200 <= resp.status < 300 else 0
    except Exception:
        return 0
