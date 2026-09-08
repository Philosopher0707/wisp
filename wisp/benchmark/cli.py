"""CLI glue for ``wisp bench``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def run_bench(argv: list[str], core_factory=None) -> int:
    """Parse bench args, run the matrix, print the scoreboard."""
    parser = argparse.ArgumentParser(
        prog="wisp bench",
        description="Benchmark Wisp against local models on deterministic tasks",
    )
    parser.add_argument(
        "--models", "-m",
        default="",
        help="Comma-separated model names (default: config model)",
    )
    parser.add_argument(
        "--tasks", "-t",
        default="",
        help="Comma-separated task ids (default: full suite)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Per-task wall-clock timeout in seconds (default 300)",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="Directory for isolated task workspaces (default: .wisp/bench)",
    )
    parser.add_argument(
        "--predictions",
        default=None,
        help="Write SWE-bench-format predictions JSONL here "
             "({instance_id, model_patch, model_name} per line)",
    )
    parser.add_argument(
        "--instances",
        default=None,
        help="SWE-bench-format instances JSONL to run instead of builtin tasks",
    )
    args = parser.parse_args(argv)

    from wisp.benchmark.runner import make_ollama_core_factory, run_benchmark
    from wisp.benchmark.tasks import tasks_by_ids
    from wisp.config import load_config

    config = load_config()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        models = [config.get("model") or ""]

    try:
        if args.instances:
            from wisp.benchmark.swebench import tasks_from_jsonl
            tasks = tasks_from_jsonl(args.instances)
        else:
            tasks = tasks_by_ids([t.strip() for t in args.tasks.split(",") if t.strip()])
    except ValueError as exc:
        print(f"✗ {exc}")
        return 2

    workdir = Path(args.workdir) if args.workdir else Path(".wisp/bench")
    if core_factory is None:
        core_factory = make_ollama_core_factory(config)

    def _progress(res):
        from wisp.benchmark.report import render_result_line

        print(render_result_line(res))
        sys.stdout.flush()

    results = asyncio.run(
        run_benchmark(
            models=models,
            tasks=tasks,
            core_factory=core_factory,
            timeout_s=args.timeout,
            workdir=workdir,
            on_result=_progress,
        )
    )

    print()
    from wisp.benchmark.runner import aggregate
    from wisp.benchmark.report import render_scoreboard

    print(render_scoreboard(aggregate(models, results)))

    if args.predictions:
        write_predictions_jsonl(args.predictions, models, results)

    failed = any(not r.passed for r in results)
    return 1 if failed else 0


def write_predictions_jsonl(path: str | Path, models: list[str],
                            results) -> Path:
    """Write SWE-bench-format predictions: one JSON per line with exactly
    {instance_id, model_patch, model_name} — the keys the official harness
    scores. Returns the written path."""
    import json

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for res in results:
            fh.write(json.dumps({
                "instance_id": res.instance_id or res.task_id,
                "model_patch": res.model_patch or "",
                "model_name": res.model,
            }, ensure_ascii=False) + "\n")
    return out
