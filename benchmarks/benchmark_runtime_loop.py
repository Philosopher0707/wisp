#!/usr/bin/env python3
"""Benchmark the live runtime turn loop (fake core, no providers).

Measures what the task requires — nothing else:
  1. Cold vs. warm turn latency (ms).
  2. Memory footprint growth across a 20-turn simulated run.
  3. Throughput of concurrent tool-bearing turns.

An optional cProfile breakdown (--profile) attributes overhead to
runtime helpers (_record_session_memory, persistence, telemetry).

Usage:
    python3 benchmarks/benchmark_runtime_loop.py [--turns N] [--profile]
    python3 benchmarks/benchmark_runtime_loop.py --concurrent 8 --turns 20
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import json
import pstats
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")


class FakeCore:
    """Stand-in for WispAgentCore: one content + tool round-trip per turn."""

    def __init__(self, tool_payload_chars: int = 4000):
        self.tool_payload_chars = tool_payload_chars
        self.turns = 0

    async def turn(self, session, prompt, approval_handler=None, steering_drain=None):
        self.turns += 1
        yield {"type": "content", "text": f"working on: {prompt[:40]}"}
        call = {"name": "read_file",
                "arguments": {"path": f"file_{self.turns % 5}.py"}}
        yield {"type": "tool_call", "name": "read_file", "arguments": call["arguments"]}
        yield {"type": "tool_result", "name": "read_file",
               "result": "x" * self.tool_payload_chars}


def _make_runtime(tmp: str, core: FakeCore):
    from wisp.config import WispConfig
    from wisp.core.runtime import AgentRuntime
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry

    config = WispConfig()
    config = config.replace(workspace=tmp)
    return AgentRuntime(
        store=UnifiedStore(Path(tmp) / "wisp.db"),
        security=SecurityPolicy(),
        extensions=ExtensionHost(),
        telemetry=Telemetry(),
        core_factory=lambda: core,
        config=config,
    )


async def _run_turn(runtime, session, prompt: str) -> float:
    t0 = time.perf_counter()
    async for _ in runtime.run_turn(session, prompt):
        pass
    return (time.perf_counter() - t0) * 1000.0


def _rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports kilobytes.
    return rss / (1024.0 * 1024.0) if sys.platform == "darwin" else rss / 1024.0


def _bench_latency(turns: int, payload: int) -> dict[str, Any]:
    async def _go():
        with tempfile.TemporaryDirectory() as td:
            core = FakeCore(tool_payload_chars=payload)
            runtime = _make_runtime(td, core)
            session = await runtime.get_or_create_session(
                session_id="bench", model="mock", workspace=td)
            latencies = []
            for i in range(turns):
                latencies.append(await _run_turn(runtime, session, f"task number {i}"))
            return latencies, session

    latencies, session = asyncio.run(_go())
    msg_bytes = len(json.dumps(session.get("messages", [])))
    try:
        from wisp.core.context_manager import live_tool_bytes

        live_bytes = live_tool_bytes(session.get("messages", []))
    except ImportError:
        live_bytes = -1
    return {
        "cold_ms": latencies[0],
        "warm_median_ms": statistics.median(latencies[1:]),
        "warm_max_ms": max(latencies[1:]),
        "messages": len(session.get("messages", [])),
        "message_bytes": msg_bytes,
        "live_tool_bytes": live_bytes,
    }


def _bench_history(prior_messages: int, payload: int) -> dict[str, Any]:
    """Turn latency with a pre-grown session (load + stringify + save cost)."""
    import json as _json

    async def _go():
        with tempfile.TemporaryDirectory() as td:
            core = FakeCore(tool_payload_chars=payload)
            runtime = _make_runtime(td, core)
            session = await runtime.get_or_create_session(
                session_id="bench", model="mock", workspace=td)
            # Grow history with realistic tool-call shapes, then persist.
            for i in range(prior_messages // 3):
                session["messages"].append({"role": "user", "content": f"q{i}"})
                session["messages"].append({
                    "role": "assistant", "content": "",
                    "tool_calls": [{"id": f"c{i}", "type": "function",
                                    "function": {"name": "read_file",
                                                 "arguments": _json.dumps({"path": "a.py"})}}]})
                session["messages"].append({"role": "tool", "name": "read_file",
                                            "content": "y" * payload})
            runtime.store.save_session(session)
            # Fresh runtime = cold load path, like a new CLI invocation.
            runtime2 = _make_runtime(td, core)
            t0 = time.perf_counter()
            session2 = await runtime2.get_or_create_session(
                session_id="bench", model="mock", workspace=td)
            load_ms = (time.perf_counter() - t0) * 1000.0
            turn_ms = await _run_turn(runtime2, session2, "next question")
            return load_ms, turn_ms, len(_json.dumps(session2.get("messages", [])))

    load_ms, turn_ms, total_bytes = asyncio.run(_go())
    return {"load_ms": load_ms, "turn_ms": turn_ms, "total_bytes": total_bytes}


def _bench_concurrent(sessions: int, turns_each: int, payload: int) -> dict[str, Any]:
    async def _go():
        with tempfile.TemporaryDirectory() as td:
            core = FakeCore(tool_payload_chars=payload)
            runtime = _make_runtime(td, core)
            stores = []
            for s in range(sessions):
                stores.append(await runtime.get_or_create_session(
                    session_id=f"bench-{s}", model="mock", workspace=td))
            t0 = time.perf_counter()

            async def _worker(sid: int):
                for i in range(turns_each):
                    await _run_turn(runtime, stores[sid], f"session {sid} task {i}")

            await asyncio.gather(*(_worker(s) for s in range(sessions)))
            return (time.perf_counter() - t0)

    elapsed = asyncio.run(_go())
    total = sessions * turns_each
    return {"turns": total, "elapsed_s": elapsed, "turns_per_sec": total / elapsed}


def _bench_profile(turns: int, payload: int) -> str:
    async def _go():
        with tempfile.TemporaryDirectory() as td:
            core = FakeCore(tool_payload_chars=payload)
            runtime = _make_runtime(td, core)
            session = await runtime.get_or_create_session(
                session_id="bench", model="mock", workspace=td)
            for i in range(turns):
                await _run_turn(runtime, session, f"profile task {i}")

    profiler = cProfile.Profile()
    profiler.enable()
    asyncio.run(_go())
    profiler.disable()
    buf = io.StringIO()
    stats = pstats.Stats(profiler, stream=buf).sort_stats("cumulative")
    stats.print_stats(18)
    return buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--payload", type=int, default=4000)
    parser.add_argument("--concurrent", type=int, default=0,
                        help="N sessions x --turns turns via gather (0 = skip)")
    parser.add_argument("--history", type=int, default=0,
                        help="pre-grown message count for load-path bench (0 = skip)")
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()

    rss_before = _rss_mb()
    lat = _bench_latency(args.turns, args.payload)
    rss_after = _rss_mb()
    print(f"turns={args.turns} payload={args.payload}B")
    print(f"  cold={lat['cold_ms']:.1f}ms warm_median={lat['warm_median_ms']:.1f}ms "
          f"warm_max={lat['warm_max_ms']:.1f}ms")
    print(f"  messages={lat['messages']} message_bytes={lat['message_bytes']} "
          f"live_tool_bytes={lat['live_tool_bytes']} "
          f"rss={rss_before:.1f}MB -> {rss_after:.1f}MB "
          f"(+{(rss_after - rss_before):.1f}MB)")
    if args.history > 0:
        hist = _bench_history(args.history, args.payload)
        print(f"  history({args.history} msgs): load={hist['load_ms']:.1f}ms "
              f"turn={hist['turn_ms']:.1f}ms total_bytes={hist['total_bytes']}")
    if args.concurrent > 0:
        conc = _bench_concurrent(args.concurrent, args.turns, args.payload)
        print(f"  concurrent: {conc['turns']} turns / {conc['elapsed_s']:.2f}s = "
              f"{conc['turns_per_sec']:.1f} turns/s")
    if args.profile:
        print("--- cProfile (cumulative top) ---")
        print(_bench_profile(args.turns, args.payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
