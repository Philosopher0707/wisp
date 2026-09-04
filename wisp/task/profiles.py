"""Profiles (M6): named config postures. Each profile is a dict of
WispConfig overrides + a policy posture note. ci-headless is safer by
default than interactive use (exec denied, network off — the expired-trim
posture from M4, applied deliberately).
"""
from __future__ import annotations
from typing import Any

PROFILES: dict[str, dict[str, Any]] = {
    "personal": {
        "permission_mode": "auto_edit",
        "auto_approve": False,
        "note": "Interactive default: writes auto-approved, exec asks.",
    },
    "enterprise-managed": {
        "permission_mode": "ask_all",
        "auto_approve": False,
        "note": "Managed device: every mutation asks; org bundle governs.",
    },
    "offline-secure": {
        "permission_mode": "ask_all",
        "auto_approve": False,
        "note": "No network: local models only; approvals as managed.",
    },
    "read-only-review": {
        "permission_mode": "read_only",
        "auto_approve": False,
        "note": "Review posture: mutations denied, reads free.",
    },
    "ci-headless": {
        "permission_mode": "ask_all",
        "auto_approve": False,
        "deny_exec": True,
        "network_off": True,
        "note": "Headless CI: safest by default; exec denied, network off.",
    },
}


def apply_profile(config: Any, name: str) -> Any:
    """Return config with the profile's overrides applied (via replace)."""
    if name not in PROFILES:
        raise ValueError(
            f"unknown profile {name!r} (choose: {', '.join(sorted(PROFILES))})")
    overrides = {k: v for k, v in PROFILES[name].items() if k != "note"}
    deny_exec = overrides.pop("deny_exec", False)
    network_off = overrides.pop("network_off", False)
    updated = config.replace(**overrides)
    # Posture flags ride as attributes for policy layers to consult.
    object.__setattr__(updated, "profile_deny_exec", deny_exec)
    object.__setattr__(updated, "profile_network_off", network_off)
    object.__setattr__(updated, "profile_name", name)
    return updated
