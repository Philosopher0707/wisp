"""Benchmark concurrent subagent throughput (fake workers, no providers).

Usage:
    python3 scripts/bench_subagent_pool.py [--fanout N] [--concurrency C]
                                           [--latency-ms M] [--timeout S]

Reports tasks/sec, peak in-flight vs cap, and reducer totals. The semaphore
cap must never be exceeded regardless of fanout breadth.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

sys.path.insert(0, ".")

from wisp.core.subagent.coordinator import Coordinator, CoordinatorConfig
from wisp.core.subagent.protocol import ExecutionPolicy


async def _main(fanout: int, concurrency: int, latency_ms: float, timeout_s: float) -> int:
    in_flight = 0
    peak = 0

    async def _worker(frame, emit) -> dict:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(latency_ms / 1000.0)
            return {"task_id": frame.task_id, "status": "SUCCESS", "findings": [],
                    "token_usage": {"prompt": 100, "completion": 50}}
        finally:
            in_flight -= 1

    coord = Coordinator(
        worker_fn=_worker,
        config=CoordinatorConfig(
            default_policy=ExecutionPolicy(max_concurrent=concurrency, timeout_s=timeout_s)),
    )
    frames = [coord.build_frame(f"bench task {i}", role="explorer") for i in range(fanout)]
    t0 = time.monotonic()
    reduced = await coord.fanout(frames)
    elapsed = time.monotonic() - t0
    ok = reduced.succeeded == fanout and peak <= concurrency
    print(f"fanout={fanout} concurrency={concurrency} latency_ms={latency_ms}")
    print(f"  elapsed={elapsed:.2f}s throughput={fanout / elapsed:.1f} tasks/s")
    print(f"  peak_in_flight={peak} (cap {concurrency}) tokens={reduced.total_tokens}")
    print(f"  succeeded={reduced.succeeded} failed={reduced.failed} timed_out={reduced.timed_out}")
    print("PASS" if ok else "FAIL: cap exceeded or tasks lost")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fanout", type=int, default=32)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--latency-ms", type=float, default=25.0)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    return asyncio.run(_main(args.fanout, args.concurrency, args.latency_ms, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
