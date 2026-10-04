"""`python -m wisp_net`: run the network platform as an MCP server, or a headless demo."""

from __future__ import annotations

import argparse
import json
import logging
import os
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
    for name in ("mcp", "demo", "serve"):
        p = sub.add_parser(name)
        p.add_argument("--scenario", help="bundled scenario name or path to a scenario JSON")
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--leaves", type=int, default=4)
        p.add_argument("--spines", type=int, default=2)
    mcp = sub.choices["mcp"]
    mcp.add_argument("--lab-control", action="store_true", help="expose fault-injection and clock tools")
    mcp.add_argument("--speed", type=float, default=1.0, help="lab seconds per wall-clock second")
    mcp.add_argument("--warmup", type=float, default=0.0, metavar="SECONDS",
                     help="advance the lab this many simulated seconds before serving (a fault already developed)")
    mcp.add_argument("--log", help="log file (the server never writes to stdout/stderr outside the protocol)")
    mcp.add_argument("--cockpit", type=int, default=0, metavar="PORT",
                     help="serve the operator cockpit on 127.0.0.1:PORT (approvals, kill switch, ledger)")
    mcp.add_argument("--ledger", help="append the change ledger to this JSONL file (default: in memory)")
    mcp.add_argument("--change-policy", help="change policy JSON (default: the bundled policies/change-policy.json)")
    mcp.add_argument("--connect", metavar="URL",
                     help="proxy to a running `serve` platform (shared network) instead of a private lab")
    serve = sub.choices["serve"]
    serve.add_argument("--port", type=int, default=8750)
    serve.add_argument("--speed", type=float, default=1.0)
    serve.add_argument("--ledger")
    serve.add_argument("--change-policy")
    serve.add_argument("--autonomy", choices=["observe", "diagnose", "propose", "act"], default="diagnose")
    serve.add_argument("--agent-cmd", default="wisp run {prompt} --skill net-orchestrator",
                       help="command for one incident's agent turn; {prompt} is replaced")
    serve.add_argument("--debounce", type=float, default=20.0, help="lab seconds to gather alerts into one incident")
    serve.add_argument("--cooldown", type=float, default=600.0)
    demo = sub.choices["demo"]
    demo.add_argument("--seconds", type=float, default=300.0)
    sub.add_parser("scenarios")
    inst = sub.add_parser("install-skills", help="install the domain-agent skills where wisp discovers skills")
    inst.add_argument("--dest", default=str(Path.home() / ".agents" / "skills"))
    inst.add_argument("--force", action="store_true", help="replace skills that differ")
    ev = sub.add_parser("eval", help="score a real model against the scenarios (read-only, hermetic)")
    ev.add_argument("--model", help="model name as the provider knows it (default: WISP_MODEL)")
    ev.add_argument("--provider", help="provider name (default: WISP_PROVIDER); there is no built-in default")
    ev.add_argument("--scenario", action="append", help="limit to this scenario (repeatable)")
    ev.add_argument("--skill", default="net-orchestrator")
    ev.add_argument("--timeout", type=float, default=600.0, help="seconds per scenario")
    ev.add_argument("--samples", type=int, default=1, help="runs per scenario; with more than one, pass rates are reported")
    ev.add_argument("--jobs", type=int, default=1, help="runs in flight at once (mind the provider's rate limit)")
    ev.add_argument("--pass-env", action="append", default=[], metavar="NAME",
                    help="environment variable to forward to wisp, e.g. OPENAI_API_KEY (default: none)")
    ev.add_argument("--wisp-cmd", default="wisp", help="how to start wisp")
    ev.add_argument("--json", metavar="FILE", help="write the full scores as JSON")
    ev.add_argument("--keep", metavar="DIR", help="keep each run's raw stdout/stderr here")
    op = sub.add_parser("cockpit", help="operator commands against a running cockpit")
    op.add_argument("--port", type=int, default=8750)
    op.add_argument("action", choices=["status", "approvals", "grant", "deny", "kill", "release", "rollback",
                                       "ledger", "explain"])
    op.add_argument("target", nargs="?", help="request id (grant/deny), apply id (rollback), fingerprint (explain)")
    op.add_argument("--operator", default=os.environ.get("USER", ""))
    op.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    if args.cmd == "cockpit":
        return _cockpit(args)
    if args.cmd == "install-skills":
        return _install_skills(Path(args.dest), args.force)
    if args.cmd == "eval":
        return _eval(args)

    if args.cmd == "scenarios":
        for scenario in sorted(SCENARIOS.glob("*.json")):
            print(f"{scenario.stem:20} {json.loads(scenario.read_text()).get('description', '')}")
        return 0

    if args.cmd == "mcp" and args.connect:
        from wisp_net.governance.cockpit import agent_token_path
        from wisp_net.mcp_server import RemotePlatform, run_stdio

        run_stdio(None, remote=RemotePlatform(args.connect, agent_token_path().read_text(encoding="utf-8").strip()))
        return 0

    from wisp_net.service import NetService

    service = NetService(seed=args.seed, leaves=args.leaves, spines=args.spines, scenario=_scenario(args.scenario),
                         ledger_path=getattr(args, "ledger", None))
    if args.cmd == "serve":
        return _serve(service, args)
    if args.cmd == "mcp":
        if args.change_policy:
            from wisp_net.safety.policy import ChangePolicy

            service.change_policy = ChangePolicy.from_dict(json.loads(Path(args.change_policy).read_text()))
        if args.log:
            logging.basicConfig(filename=args.log, level=logging.INFO,
                                format="%(asctime)s %(levelname)s %(name)s %(message)s")
        from wisp_net.mcp_server import run_stdio

        if args.warmup > 0:
            service.advance(args.warmup)
        service.start_realtime(args.speed)
        cockpit = None
        if args.cockpit:
            from wisp_net.governance.cockpit import CockpitServer

            cockpit = CockpitServer(service, args.cockpit)
            cockpit.start()
        try:
            run_stdio(service, lab_control=args.lab_control)
        finally:
            if cockpit is not None:
                cockpit.stop()
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


def _serve(service: Any, args: argparse.Namespace) -> int:
    import shlex

    from wisp_net.governance.cockpit import CockpitServer, agent_token_path, token_path
    from wisp_net.loop.watcher import Watcher, subprocess_runner

    if args.change_policy:
        from wisp_net.safety.policy import ChangePolicy

        service.change_policy = ChangePolicy.from_dict(json.loads(Path(args.change_policy).read_text()))
    cockpit = CockpitServer(service, args.port)
    url = f"http://127.0.0.1:{args.port}"
    runner = subprocess_runner(shlex.split(args.agent_cmd)) if args.autonomy != "observe" else None
    service.watcher = Watcher(service, runner, args.autonomy, args.debounce, args.cooldown,
                              env={"WISP_NET_URL": url, "WISP_NET_AGENT_TOKEN_FILE": str(agent_token_path())})
    cockpit.start()
    service.start_realtime(args.speed)
    print(f"wisp-net platform on {url}  autonomy={args.autonomy}")
    print(f"  operator token: {token_path()}   agent token: {agent_token_path()}")
    print(f"  wisp connects with: python -m wisp_net mcp --connect {url}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        service.watcher.join(5)
        cockpit.stop()
        service.stop()
    return 0


AGENTS = Path(__file__).resolve().parent / "agents"


def _install_skills(dest: Path, force: bool) -> int:
    status = 0
    for skill in sorted(AGENTS.glob("*/SKILL.md")):
        target = dest / skill.parent.name / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        if target.exists() and target.read_text(encoding="utf-8") != text and not force:
            print(f"kept    {target} (differs; --force to replace)")
            status = 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"installed {target}")
    return status


def _eval(args: argparse.Namespace) -> int:
    import shlex

    from wisp.user_env import load_user_env
    from wisp_net import evaluation

    load_user_env()  # ~/.config/wisp/.env; exported variables win; this process only, never the child's HOME
    provider = args.provider or os.environ.get("WISP_PROVIDER", "")
    model = args.model or os.environ.get("WISP_MODEL", "")
    for what, value, flag in (("provider", provider, "--provider"), ("model", model, "--model")):
        if not value:
            print(f"no {what}: pass {flag} or set WISP_{what.upper()} in the environment or ~/.config/wisp/.env",
                  file=sys.stderr)
            return 2
    known = {c.scenario: c for c in evaluation.CASES}
    wanted = args.scenario or list(known)
    unknown = [name for name in wanted if name not in known]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)}; choose from {', '.join(known)}", file=sys.stderr)
        return 2
    if args.samples < 1 or args.jobs < 1:
        print("--samples and --jobs must be at least 1", file=sys.stderr)
        return 2
    try:
        scores = evaluation.run_eval([known[name] for name in wanted], samples=args.samples, jobs=args.jobs,
                                     model=model, provider=provider, skill=args.skill,
                                     wisp_cmd=shlex.split(args.wisp_cmd), timeout_s=args.timeout,
                                     passthrough=args.pass_env, keep=Path(args.keep) if args.keep else None)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(evaluation.summarize(scores))
    if args.json:
        Path(args.json).write_text(evaluation.to_json(scores), encoding="utf-8")
    return 0 if all(s.passed for s in scores) else 1


def _cockpit(args: argparse.Namespace) -> int:
    import httpx

    from wisp_net.governance.cockpit import load_or_create_token

    base = f"http://127.0.0.1:{args.port}"
    headers = {"Authorization": f"Bearer {load_or_create_token()}"}
    who = {"operator": args.operator, "note": args.reason, "reason": args.reason}
    calls: dict[str, tuple[str, str, dict[str, Any] | None]] = {
        "status": ("GET", "/status", None), "approvals": ("GET", "/approvals", None),
        "ledger": ("GET", "/ledger", None),
        "grant": ("POST", f"/approvals/{args.target}/grant", who),
        "deny": ("POST", f"/approvals/{args.target}/deny", who),
        "kill": ("POST", "/kill-switch", {**who, "engaged": True}),
        "release": ("POST", "/kill-switch", {**who, "engaged": False}),
        "rollback": ("POST", f"/changes/{args.target}/rollback", who),
        "explain": ("GET", f"/changes/{args.target}/explain", None),
    }
    method, path, body = calls[args.action]
    if args.action in ("grant", "deny", "rollback", "explain") and not args.target:
        raise SystemExit(f"{args.action} needs a target id")
    response = httpx.request(method, base + path, headers=headers, json=body, timeout=120)
    print(json.dumps(response.json(), indent=2, default=str))
    return 0 if response.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
