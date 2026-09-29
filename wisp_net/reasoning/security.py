"""Security sentinel: volumetric anomalies in flow records, and where a host is attached.

A source is anomalous when its bytes in the latest window sit far above its own recent
history (z-score over the previous windows, with a floor so a flat baseline does not make
every wobble infinite). Where a host is attached comes from the inventory (IPAM), the one
place that maps a server address to a switch port.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

Z_LIMIT = 4.0
MIN_RATIO = 3.0


def flow_anomalies(flows: Iterable[dict[str, Any]], now: float, window_s: float = 60.0,
                   baseline_windows: int = 5) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = {}
    exporters: dict[str, set[str]] = {}
    for r in flows:
        age = now - float(r["ts"])
        index = int(age // window_s)
        if index < 0 or index > baseline_windows:
            continue
        series = buckets.setdefault(r["src"], [0.0] * (baseline_windows + 1))
        series[index] += r["octets"] * 8 / window_s
        exporters.setdefault(r["src"], set()).add(f"{r['exporter']}:{r['ingress_if']}")
    out = []
    for src, series in buckets.items():
        current, history = series[0], series[1:]
        if not any(history):
            continue
        mean = sum(history) / len(history)
        std = math.sqrt(sum((h - mean) ** 2 for h in history) / len(history))
        z = (current - mean) / max(std, 0.05 * mean, 1.0)
        if z >= Z_LIMIT and current >= MIN_RATIO * mean:
            out.append({"src": src, "current_gbps": round(current / 1e9, 2), "baseline_gbps": round(mean / 1e9, 2),
                        "ratio": round(current / mean, 1), "z": round(z, 1),
                        "entering_at": sorted(exporters[src])})
    return sorted(out, key=lambda a: -a["z"])


def host_location(spec: Any, ip: str) -> dict[str, str] | None:
    for host in spec.hosts:
        if host.ip == ip:
            return {"host": host.name, "device": host.leaf, "port": host.port, "ip": host.ip}
    return None
