"""Chain vs fan benchmark (§45) — realistic mock coding-agent tasks.

Compares linear chain with fan graph on independent work. Uses a scripted
runner (no model calls) so results are deterministic and fast; measures
wall-clock latency, node calls, tokens, peak concurrency, critical path,
and cost. Run: python scripts/bench_graph.py [--branches N] [--work MS]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time

sys.path.insert(0, ".")

from wisp.graph.compat import chain_to_graph, fan_to_graph  # noqa: E402
from wisp.graph.executor import GraphExecutor  # noqa: E402
from wisp.graph.store import GraphStore  # noqa: E402
from wisp.graph.trace import quality_metrics  # noqa: E402
from wisp.graph.types import NodeStatus, NodeResult  # noqa: E402


async def _no_sleep(_: float) -> None:
    pass


def _make_runner(work_s: float, peak: dict):
    live = {"n": 0}

    async def run(node, inputs):
        if node.id == "split":
            return NodeResult("split", NodeStatus.SUCCESS, output={"slices": []})
        live["n"] += 1
        peak["v"] = max(peak["v"], live["n"])
        try:
            await asyncio.sleep(work_s)
            return NodeResult(node.id, NodeStatus.SUCCESS, output={"patch": node.id},
                              input_tokens=800, output_tokens=400, cost_usd=0.0006)
        finally:
            live["n"] -= 1
    return run


async def _bench(n: int, work_ms: int, max_c: int) -> dict:
    work_s = work_ms / 1000
    out = {}
    for name, graph in (("chain", chain_to_graph("chain", [f"task-{i}" for i in range(n)])),
                        ("fan", fan_to_graph("fan", [f"task-{i}" for i in range(n)]))):
        ws = tempfile.mkdtemp()
        peak: dict = {"v": 0}
        ex = GraphExecutor(runner=_make_runner(work_s, peak), workspace=ws,
                           max_concurrency=max_c,
                           store=GraphStore(workspace=ws), sleep=_no_sleep)
        ex.register_function("split_work", lambda i: {"slices": []})
        t0 = time.monotonic()
        r = await ex.run(graph, {})
        wall = time.monotonic() - t0
        m = quality_metrics(r)
        out[name] = {"wall_s": round(wall, 2), "peak_concurrency": peak["v"],
                     "node_calls": len(r["results_by_node"]),
                     "tokens": r["tokens"], "cost_usd": r["cost_usd"],
                     "parallelism_factor": m["parallelism_factor"]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branches", type=int, default=8)
    ap.add_argument("--work", type=int, default=50, help="ms per node")
    ap.add_argument("--max", type=int, default=8)
    args = ap.parse_args()
    res = asyncio.run(_bench(args.branches, args.work, args.max))
    c, f = res["chain"], res["fan"]
    print(f"nodes={args.branches} work={args.work}ms max_concurrency={args.max}")
    print(f"  chain: wall {c['wall_s']}s peak {c['peak_concurrency']} "
          f"tokens {c['tokens']} cost ${c['cost_usd']} pf {c['parallelism_factor']}")
    print(f"  fan:   wall {f['wall_s']}s peak {f['peak_concurrency']} "
          f"tokens {f['tokens']} cost ${f['cost_usd']} pf {f['parallelism_factor']}")
    if f["wall_s"] and c["wall_s"]:
        print(f"  speedup: {c['wall_s'] / max(f['wall_s'], 1e-9):.1f}x wall-clock; "
              f"token cost identical ({f['tokens'] == c['tokens']})")
    print("  note: fan wins on independent work; a single tightly-coupled task "
          "favours the chain (no fan-out overhead).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
