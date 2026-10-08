"""`python -m wisp.dashboard serve` shows the dashboard; `python -m wisp.dashboard bench` runs the benchmark (it spends tokens, so only you start it)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _checkout_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m wisp.dashboard", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="serve the read-only dashboard on 127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--bench-dir", default="", help="where benchmark results live (default: $WISP_BENCH_DIR or ~/.local/state/wisp/bench)")
    s.add_argument("--workspace", action="append", default=[], help="a workspace whose .wisp data to include (repeatable; default: scan common roots)")
    b = sub.add_parser("bench", help="run the benchmark and write one line per attempt")
    b.add_argument("--model", default="", help="default: WISP_MODEL from this environment, then from ~/.config/wisp/.env")
    b.add_argument("--provider", default="", help="default: WISP_PROVIDER")
    b.add_argument("--api-base", default="", help="default: WISP_API_BASE")
    b.add_argument("--key-from", default="", help="NAME of the environment variable or ~/.config/wisp/.env line that holds the key; default WISP_API_KEY when it is set; the key is never printed or written")
    b.add_argument("--label", default="default", help="the harness configuration being measured, e.g. default or core-off")
    b.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="environment for the child wisp (how a configuration is chosen); repeatable")
    b.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    b.add_argument("--repeats", type=int, default=1)
    b.add_argument("--timeout", type=int, default=900)
    b.add_argument("--infra-retries", type=int, default=3)
    b.add_argument("--backoff", type=float, default=45.0)
    b.add_argument("--wisp-path", default="", help="the checkout whose wisp the child runs (default: this one); use a clean worktree to measure a commit")
    b.add_argument("--min-free-mb", type=int, default=500, help="refuse to run when the temp disk has less free space than this")
    b.add_argument("--no-isolate-home", action="store_true", help="let the child use your real HOME (its memory and facts get written); default: a fresh empty HOME per attempt")
    b.add_argument("--out", default="")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "bench":
        import wisp.dashboard.bench as bench

        env: dict[str, str] = {}
        for item in args.env:
            key, sep, value = item.partition("=")
            if not sep or not key:
                print(f"--env expects KEY=VALUE, got {item!r}", file=sys.stderr)
                return 2
            env[key] = value
        import wisp.dashboard.presence as presence

        resolved = presence.resolve_model()
        model = args.model or resolved.get("model", "")
        if not model:
            print("no model: pass --model, or set WISP_MODEL in the environment or in ~/.config/wisp/.env", file=sys.stderr)
            return 2
        provider, api_base = args.provider or resolved.get("provider", ""), args.api_base or resolved.get("api_base", "")
        key_from = args.key_from or ("WISP_API_KEY" if presence.env_has("WISP_API_KEY") else "")
        source = "--model" if args.model else resolved.get("model_source", "")
        print(f"measuring {model} ({source}) provider={provider or 'default'} key from {key_from or 'nothing'}; this spends tokens on that provider's account", flush=True)
        spec = bench.BenchSpec(model=model, provider=provider, api_base=api_base, key_from=key_from, label=args.label, env=env,
                               tasks=[t for t in args.tasks.split(",") if t], repeats=args.repeats, timeout=args.timeout,
                               infra_retries=args.infra_retries, backoff_s=args.backoff, wisp_path=args.wisp_path or _checkout_root(),
                               isolate_home=not args.no_isolate_home, min_free_mb=args.min_free_mb)
        out = Path(args.out).expanduser() if args.out else bench.default_results_dir()
        try:
            path = bench.run_bench(spec, out, log=lambda s: print(s, flush=True))
        except bench.DiskTooFull as exc:
            print(f"stopped: {exc}", file=sys.stderr)
            return 3
        print(f"results: {path}")
        return 0
    import wisp.dashboard.server as server

    return server.serve(port=args.port, bench_dir=Path(args.bench_dir).expanduser() if args.bench_dir else None, workspaces=[Path(w).expanduser() for w in args.workspace])


if __name__ == "__main__":
    raise SystemExit(main())
