"""Compliance and configuration audit: drift from the golden config, and software advisories.

Drift is measured on what devices *report* over gNMI, never on the simulator's memory,
so it finds exactly what an auditor with read access would. Every deviation names the
rule, its class (security | routing | cosmetic), the device, the path, what is there and
what should be.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wisp_net.paths import matches, parse_path

POLICIES = Path(__file__).resolve().parent.parent / "policies"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((POLICIES / name).read_text(encoding="utf-8"))
    return data


def config_drift(gnmi: Any, golden: dict[str, Any], devices: list[str] | None = None) -> dict[str, Any]:
    targets = devices or gnmi.targets()
    findings: list[dict[str, Any]] = []
    unreachable: list[str] = []
    for dev in targets:
        try:
            leaves = {n.path: n.value for n in gnmi.get(dev, "/")}
        except Exception:
            unreachable.append(dev)
            continue
        for rule in golden["rules"]:
            pattern = parse_path(rule["path"])
            hits = {p: v for p, v in leaves.items() if matches(pattern, parse_path(p))}
            base = {"rule": rule["id"], "class": rule["class"], "device": dev}
            if rule.get("absent"):
                for path, value in sorted(hits.items()):
                    findings.append({**base, "path": path, "found": value, "expected": "absent"})
            elif "set_equals" in rule:
                found = sorted(str(v) for v in hits.values())
                if found != sorted(rule["set_equals"]):
                    findings.append({**base, "path": rule["path"], "found": found,
                                     "expected": sorted(rule["set_equals"])})
            elif not hits:
                findings.append({**base, "path": rule["path"], "found": None, "expected": rule["equals"]})
            else:
                for path, value in sorted(hits.items()):
                    if value != rule["equals"]:
                        findings.append({**base, "path": path, "found": value, "expected": rule["equals"]})
    by_class: dict[str, int] = {}
    for f in findings:
        by_class[f["class"]] = by_class.get(f["class"], 0) + 1
    return {"devices_checked": len(targets) - len(unreachable), "unreachable": unreachable,
            "compliant": not findings, "by_class": by_class, "findings": findings}


def advisories(twin: Any, table: dict[str, Any]) -> dict[str, Any]:
    exposed: list[dict[str, Any]] = []
    for dev, st in sorted(twin.devices.items()):
        software = str(st.system.get("software-version") or "")
        nos, _, version = software.partition("-")
        for adv in table["advisories"]:
            if adv["nos"] == nos and version in adv["affected"]:
                exposed.append({"device": dev, "software": software, "advisory": adv["id"],
                                "severity": adv["severity"], "fixed_in": adv["fixed_in"], "summary": adv["summary"]})
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    exposed.sort(key=lambda e: (order.get(e["severity"], 9), e["device"]))
    return {"exposed": exposed, "devices_exposed": sorted({e["device"] for e in exposed}),
            "source": table.get("description", "")}
