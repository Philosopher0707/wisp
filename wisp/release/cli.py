"""Release CLI (M7 T2): lock/verify-deps/sbom/licenses/health/diagnostics.
Thin adapters; --out FILE for sbom/diagnostics. Exit 0 ok, 1 problems,
2 usage.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import TextIO


def _out_path(args: list[str]) -> Path | None:
    if "--out" in args:
        return Path(args[args.index("--out") + 1])
    return None


def _cmd_lock(args: list[str], out: TextIO) -> int:
    from wisp.release.lock import declared_deps, generate_lock
    lines = generate_lock(declared_deps())
    dest = _out_path(args) or Path("requirements.lock")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} pins -> {dest}", file=out)
    return 0


def _cmd_verify_deps(args: list[str], out: TextIO) -> int:
    from wisp.release.lock import verify_lock
    problems = verify_lock()
    if not problems:
        print("dependencies satisfy the lock", file=out)
        return 0
    for p in problems:
        print(f"  ! {p}", file=out)
    return 1


def _cmd_sbom(args: list[str], out: TextIO) -> int:
    from wisp.release.sbom import generate_sbom
    from wisp.release.diagnostics import _wisp_version
    sbom = generate_sbom(_wisp_version())
    dest = _out_path(args)
    if dest is None:
        print(json.dumps(sbom, indent=2, sort_keys=True), file=out)
    else:
        dest.write_text(json.dumps(sbom, indent=2, sort_keys=True),
                        encoding="utf-8")
        print(f"wrote {len(sbom['components'])} components -> {dest}", file=out)
    return 0


def _cmd_licenses(args: list[str], out: TextIO) -> int:
    from wisp.release.lock import audit_licenses, declared_deps
    bad = audit_licenses([n for n, _ in declared_deps()])
    if not bad:
        print("all declared licenses on the allowlist", file=out)
        return 0
    for name, lic in bad:
        print(f"  ! {name}: {lic}", file=out)
    return 1


def _cmd_health(args: list[str], out: TextIO) -> int:
    from wisp.release.health import health_check
    results = health_check()
    ok = True
    for r in results:
        mark = "ok" if r["ok"] else "FAIL"
        print(f"[{mark}] {r['name']}: {r['detail']}", file=out)
        ok = ok and r["ok"]
    return 0 if ok else 1


def _cmd_diagnostics(args: list[str], out: TextIO) -> int:
    from wisp.release.diagnostics import write_bundle
    dest = _out_path(args) or Path("wisp-diagnostics.json")
    write_bundle(dest, config={"source": "cli"})
    print(f"wrote redacted bundle -> {dest}", file=out)
    return 0


_COMMANDS = {
    "lock": _cmd_lock,
    "verify-deps": _cmd_verify_deps,
    "sbom": _cmd_sbom,
    "licenses": _cmd_licenses,
    "health": _cmd_health,
    "diagnostics": _cmd_diagnostics,
}


def main(argv: list[str], out: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    if not argv or argv[0] not in _COMMANDS:
        print(f"usage: wisp release <{'|'.join(sorted(_COMMANDS))}> [--out FILE]",
              file=out)
        return 2
    return _COMMANDS[argv[0]](argv[1:], out)
