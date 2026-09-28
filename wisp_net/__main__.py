"""`python -m wisp_net`: run the network platform as an MCP server, or a headless demo."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

SCENARIOS = Path(__file__).resolve().parent / "scenarios"


def _scenario(name: str | None) -> list[dict[str, Any]]:
    if not name:
        return []
    from wisp_net.service import load_scenario

    path = Path(name)
    if not path.exists():
        path = SCENARIOS / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"no scenario {name!r}; bundled: {', '.join(p.stem for p in sorted(SCENARIOS.glob('*.json')))}")
    return load_scenario(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m wisp_net", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("mcp", "demo"):
        p = sub.add_parser(name)
        p.add_argument("--scenario", help="bundled scenario name or path to a scenario JSON")
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--leaves", type=int, default=4)
        p.add_argument("--spines", type=int, default=2)
    mcp = sub.choices["mcp"]
    mcp.add_argument("--lab-control", action="store_true", help="expose fault-injection and clock tools")
    mcp.add_argument("--speed", type=float, default=1.0, help="lab seconds per wall-clock second")
    mcp.add_argument("--log", help="log file (the server never writes to stdout/stderr outside the protocol)")
    demo = sub.choices["demo"]
    demo.add_argument("--seconds", type=float, default=300.0)
    sub.add_parser("scenarios")
    args = parser.parse_args(argv)

    if args.cmd == "scenarios":
        for scenario in sorted(SCENARIOS.glob("*.json")):
            print(f"{scenario.stem:20} {json.loads(scenario.read_text()).get('description', '')}")
        return 0

    from wisp_net.service import NetService

    service = NetService(seed=args.seed, leaves=args.leaves, spines=args.spines, scenario=_scenario(args.scenario))
    if args.cmd == "mcp":
        if args.log:
            logging.basicConfig(filename=args.log, level=logging.INFO,
                                format="%(asctime)s %(levelname)s %(name)s %(message)s")
        from wisp_net.mcp_server import run_stdio

        service.start_realtime(args.speed)
        try:
            run_stdio(service, lab_control=args.lab_control)
        finally:
            service.stop()
        return 0

    started = time.monotonic()
    service.advance(args.seconds)
    status = service.status()
    print(f"lab {status['lab_time']}  devices {status['reporting']}/{status['devices']}  "
          f"links down {status['links_down']}/{status['links']}  ({time.monotonic() - started:.1f}s wall)")
    for a in service.alert_list("all"):
        print(f"  [{a['state']:8}] {a['severity']:8} {a['rule']:18} x{a['occurrences']:<3} {a['message']}")
    reach = service.reachability()
    stale = reach["verified_on_stale_state"]
    print(f"reachability: {reach['delivered']}/{reach['pairs_checked']} delivered"
          + (f" (via last-known state of {', '.join(stale)})" if stale else ""))
    for b in reach["broken"][:10]:
        print(f"  {b['source']} -> {b['prefix']}: {b['outcome']} {b['why']}")
    service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
