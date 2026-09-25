#!/usr/bin/env python3
"""NEXT-mission benchmark driver.

Runs the deterministic coding benchmark suite against one or more models
through a REAL provider, and writes machine-readable results.

Why this exists rather than ``wisp bench``: the CLI passes
``load_config()`` (the raw file dict) into ``make_ollama_core_factory``,
which then re-hydrates a ``WispConfig`` from it — so any key present in
``~/.config/wisp/config.json`` overrides the env-resolved value. This
driver constructs the typed config directly, so ``WISP_*`` env vars are
the authority.

Usage:
    env -u PYTHONPATH WISP_OLLAMA_URL=... WISP_MAX_TOKENS=65536 \
        .venv/bin/python scripts/next_bench.py \
        --models nemotron-3-ultra:cloud \
        --tasks create-function,fix-off-by-one,json-edit,subagent-delegate \
        --out .workbuddy-ai/memory/next/bench-results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="next_bench")
    ap.add_argument("--models", "-m", required=True,
                    help="Comma-separated model names")
    ap.add_argument("--tasks", "-t", default="",
                    help="Comma-separated task ids (default: full suite)")
    ap.add_argument("--timeout", type=float, default=420.0)
    ap.add_argument("--workdir", default=".workbuddy-ai/memory/next/bench")
    ap.add_argument("--out", default=".workbuddy-ai/memory/next/bench-results.json")
    args = ap.parse_args(argv)

    from wisp.benchmark.runner import aggregate, make_ollama_core_factory, run_benchmark
    from wisp.benchmark.tasks import tasks_by_ids
    from wisp.config import WispConfig

    cfg = WispConfig()
    print(f"[next_bench] ollama_url={cfg.ollama_url} model={cfg.model} "
          f"max_tokens={cfg.max_tokens} provider={cfg.provider}", flush=True)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    tasks = tasks_by_ids([t.strip() for t in args.tasks.split(",") if t.strip()])
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    factory = make_ollama_core_factory(cfg)
    rows: list[dict] = []
    t0 = time.monotonic()

    def _on_result(res) -> None:
        rows.append({
            "model": res.model,
            "task_id": res.task_id,
            "status": res.status(),
            "passed": res.passed,
            "timed_out": res.timed_out,
            "error": res.error,
            "verify_detail": res.verify_detail,
            "duration_s": round(res.duration_s, 1),
            "tool_calls": res.stats.tool_calls if res.stats else None,
            "ran_tests": bool(res.stats and res.stats.ran_tests),
            "surrendered": bool(res.stats and getattr(res.stats, "surrendered", False)),
            "files_touched": sorted({
                ln[6:].strip() for ln in (res.model_patch or "").splitlines()
                if ln.startswith("+++ b/")
            }),
            "patch_lines": len((res.model_patch or "").splitlines()),
            "patch_applies": res.patch_applies,
        })
        print(f"  -> {res.task_id:<24} {res.status():<8} {res.duration_s:6.1f}s "
              f"tools={rows[-1]['tool_calls']} {res.error or res.verify_detail}"[:220],
              flush=True)

    results = asyncio.run(run_benchmark(
        models=models, tasks=tasks, core_factory=factory,
        timeout_s=args.timeout, workdir=workdir, on_result=_on_result,
    ))

    cards = aggregate(models, results)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "config": {"ollama_url": cfg.ollama_url, "max_tokens": cfg.max_tokens,
                   "provider": cfg.provider},
        "wall_clock_s": round(time.monotonic() - t0, 1),
        "rows": rows,
        "scorecards": [
            {"model": c.model, "passed": c.passed, "failed": c.failed,
             "timed_out": c.timed_out, "surrendered": c.surrendered,
             "total_duration_s": round(c.total_duration_s, 1)}
            for c in cards
        ],
    }, indent=2), encoding="utf-8")

    print()
    for c in cards:
        print(f"{c.model}: {c.passed} passed / {c.failed} failed "
              f"/ {c.timed_out} timeout / {c.surrendered} surrendered")
    print(f"wrote {out}")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
