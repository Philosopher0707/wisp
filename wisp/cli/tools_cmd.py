"""``wisp tools``: what an agent is offered, and why a tool is missing.

Read-only. It prints the tools a parent or a role-child will be shown in a provider request and, for each tool that is not, the
first stage that removed it (role list, child permission mode, tool profile, capability partition) and why. The answer is
computed by ``wisp.core.tool_surface``, which composes the real filters, so it cannot disagree with what the agent gets.

Only built-in tools are listed: extension (MCP, skill) tools depend on the live session.
"""

from __future__ import annotations

import argparse
import json

from wisp.config import WispConfig
from wisp.core.tool_surface import explain_surface
from wisp.multi_agent.roles import ROLE_CONFIGS
from wisp.tools.registry import TOOL_SCHEMAS

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    cfg = WispConfig()
    roles = sorted(str(r) for r in ROLE_CONFIGS)
    parser = argparse.ArgumentParser(prog="wisp tools", description="What an agent is offered, and why a tool is missing.")
    parser.add_argument("--role", default="parent", help=f"parent (default) or a subagent role: {', '.join(roles)}")
    parser.add_argument("--mode", default=str(getattr(cfg.permission_mode, "value", cfg.permission_mode)),
                        help="permission mode (default: your configured mode)")
    parser.add_argument("--profile", default=cfg.tool_profile, help="tool profile: core or full (default: configured)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    names = [s.get("function", {}).get("name", "") for s in TOOL_SCHEMAS]
    if args.role == "parent":
        surface = explain_surface(names, profile=args.profile, permission_mode=args.mode,
                                  capability_filtering=bool(cfg.capability_filtering))
    elif args.role in roles:
        surface = explain_surface(names, role_tools=list(ROLE_CONFIGS[args.role].allowed_tools), child=True,
                                  permission_mode=args.mode, profile=args.profile,
                                  capability_filtering=bool(cfg.capability_filtering))
    else:
        print(f"wisp tools: unknown role {args.role!r}; expected parent or one of {', '.join(roles)}")
        return 2

    if args.json:
        print(json.dumps({
            "role": args.role, "mode": args.mode, "profile": args.profile,
            "offered": list(surface.offered),
            "absent": [{"tool": a.tool, "stage": a.stage, "reason": a.reason} for a in surface.absent],
        }, indent=2))
        return 0

    print(f"{args.role} · mode={args.mode} · profile={args.profile}")
    print(f"\noffered ({len(surface.offered)}): {', '.join(surface.offered)}")
    if surface.absent:
        print(f"\nnot offered ({len(surface.absent)}), grouped by why (the switches you can change come first):")
        order = {"mode": 0, "profile": 1, "capability": 2, "subagent": 3, "role": 4}
        groups: dict[tuple[str, str], list[str]] = {}
        for a in surface.absent:
            groups.setdefault((a.stage, a.reason), []).append(a.tool)
        for (stage, reason), tools in sorted(groups.items(), key=lambda kv: order.get(kv[0][0], 9)):
            shown = ", ".join(tools[:14]) + (f", ... ({len(tools)} in all)" if len(tools) > 14 else "")
            print(f"  [{stage}] {reason}\n      {shown}")
    return 0
