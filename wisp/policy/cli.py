"""Admin CLI for policy bundles (M4 T3): inspect/verify/explain/dry-run/
health/import/export. Thin adapter over wisp.policy (unit-tested); wired
as `wisp policy ...` via cmd_policy in __main__.py.

Environment:
  WISP_POLICY_BUNDLE  path to bundle.json (+ .sig sibling)
  WISP_POLICY_PUBKEY  base64 org public key
  WISP_POLICY_CACHE   managed-mode cache dir (default ~/.wisp/policy)
"""
from __future__ import annotations
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, TextIO

from wisp.policy.explain import dry_run, explain_denial
from wisp.policy.loader import (
    _read_bundle_file,
    load_local,
    load_managed,
)


def _bundle_path(args: list[str]) -> Path | None:
    if "--bundle" in args:
        return Path(args[args.index("--bundle") + 1])
    env = os.environ.get("WISP_POLICY_BUNDLE")
    return Path(env) if env else None


def _pubkey(args: list[str]) -> str:
    if "--pubkey" in args:
        return args[args.index("--pubkey") + 1]
    return os.environ.get("WISP_POLICY_PUBKEY", "")


def _cache_dir() -> Path:
    return Path(os.environ.get("WISP_POLICY_CACHE",
                               Path.home() / ".wisp" / "policy"))


def _load_effective(args: list[str]):
    """Load + merge local file over empty base (single-layer view)."""
    path = _bundle_path(args)
    pub = _pubkey(args)
    if path is None or not pub:
        raise SystemExit("set WISP_POLICY_BUNDLE and WISP_POLICY_PUBKEY (or --bundle/--pubkey)")
    return load_local(path, pub)


def _cmd_inspect(args: list[str], out: TextIO) -> int:
    eff = _load_effective(args)
    path = _bundle_path(args)
    assert path is not None
    bundle, _ = _read_bundle_file(path)
    print(f"org: {eff.org_id}", file=out)
    print(f"bundle_version={bundle.bundle_version} "
          f"revocation_seq={bundle.revocation_seq}", file=out)
    print(f"expires_at={bundle.expires_at} expired={bundle.is_expired()}", file=out)
    print(f"approved_models={','.join(eff.approved_models) or '(none)'}", file=out)
    print(f"mcp_allowlist={','.join(eff.mcp_allowlist) or '(none)'}", file=out)
    print(f"plugin_allowlist={','.join(eff.plugin_allowlist) or '(none)'}", file=out)
    print(f"approval_matrix={json.dumps(eff.approval_matrix, sort_keys=True)}", file=out)
    return 0


def _cmd_verify(args: list[str], out: TextIO) -> int:
    try:
        eff = _load_effective(args)
    except ValueError as e:
        print(f"INVALID: {e}", file=out)
        return 1
    print(f"valid signature (org={eff.org_id})", file=out)
    return 0


def _cmd_explain(args: list[str], out: TextIO) -> int:
    if not args:
        print("usage: wisp policy explain TOOL [--args JSON]", file=out)
        return 2
    tool = args[0]
    tool_args: dict[str, Any] = {}
    if "--args" in args:
        tool_args = json.loads(args[args.index("--args") + 1])
    eff = _load_effective(args)
    print(explain_denial(tool, tool_args, eff), file=out)
    return 0


def _cmd_dry_run(args: list[str], out: TextIO) -> int:
    if not args:
        print("usage: wisp policy dry-run TOOL [--args JSON]", file=out)
        return 2
    tool = args[0]
    tool_args: dict[str, Any] = {}
    if "--args" in args:
        tool_args = json.loads(args[args.index("--args") + 1])
    eff = _load_effective(args)
    d = dry_run(tool, tool_args, eff)
    print(f"allowed={d['allowed']} obligations={','.join(d['obligations']) or '(none)'}", file=out)
    print(d["reason"], file=out)
    return 0 if d["allowed"] else 1


def _cmd_health(args: list[str], out: TextIO) -> int:
    cache = _cache_dir()
    bundle_file = cache / "policy-bundle.json"
    if not bundle_file.exists():
        print("no cached bundle (local-only mode or never refreshed)", file=out)
        return 0
    pub = _pubkey(args)
    try:
        eff = load_managed(cache, pub, refresh_fn=None)
        bundle, _ = _read_bundle_file(bundle_file)
        print(f"cached bundle org={eff.org_id} "
              f"revocation_seq={bundle.revocation_seq} "
              f"expired={bundle.is_expired()}", file=out)
        return 0
    except ValueError as e:
        print(f"UNHEALTHY: {e}", file=out)
        return 1


def _cmd_import(args: list[str], out: TextIO) -> int:
    if not args:
        print("usage: wisp policy import PATH (air-gap bundle intake)", file=out)
        return 2
    src = Path(args[0])
    pub = _pubkey(args)
    if not pub:
        print("set WISP_POLICY_PUBKEY to verify on import", file=out)
        return 2
    bundle, sig = _read_bundle_file(src)
    from wisp.policy.bundle import verify_bundle
    if not sig or not verify_bundle(bundle, sig, pub):
        print("INVALID: signature check failed — not imported", file=out)
        return 1
    dest = _cache_dir()
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest / "policy-bundle.json")
    shutil.copy(src.with_suffix(src.suffix + ".sig"),
                dest / "policy-bundle.json.sig")
    print(f"imported revocation_seq={bundle.revocation_seq} -> {dest}", file=out)
    return 0


def _cmd_export(args: list[str], out: TextIO) -> int:
    if not args:
        print("usage: wisp policy export DIR", file=out)
        return 2
    dest = Path(args[0])
    dest.mkdir(parents=True, exist_ok=True)
    src = _cache_dir() / "policy-bundle.json"
    if not src.exists():
        local = _bundle_path(args)
        if local is None or not local.exists():
            print("nothing to export (no cache, no local bundle)", file=out)
            return 1
        src = local
    shutil.copy(src, dest / "policy-bundle.json")
    shutil.copy(src.with_suffix(src.suffix + ".sig"),
                dest / "policy-bundle.json.sig")
    print(f"exported -> {dest}", file=out)
    return 0


_COMMANDS = {
    "inspect": _cmd_inspect,
    "verify": _cmd_verify,
    "explain": _cmd_explain,
    "dry-run": _cmd_dry_run,
    "health": _cmd_health,
    "import": _cmd_import,
    "export": _cmd_export,
}


def main(argv: list[str], out: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    if not argv or argv[0] not in _COMMANDS:
        print(f"unknown policy command (choose: {', '.join(sorted(_COMMANDS))})",
              file=out)
        return 2
    try:
        return _COMMANDS[argv[0]](argv[1:], out)
    except SystemExit as e:
        print(str(e), file=out)
        return 2
