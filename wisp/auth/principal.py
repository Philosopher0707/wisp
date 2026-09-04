"""Principal model (M2 authority layer).

Root of identity is local: OS user + workspace + profile (ADR
2026-09-04-local-identity). Works fully offline; org identity attaches from
a verified bundle when present. Subagent principals derive strictly
narrower capabilities — never inherited root authority.
"""
from __future__ import annotations
import getpass
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import FrozenSet, Optional


class PrincipalKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SUBAGENT = "subagent"
    DELEGATED = "delegated"


@dataclass(frozen=True)
class Principal:
    """An actor that tool effects are attributed to.

    capabilities=None means unbounded (local human root, subject to
    approval/policy). Any bounded set restricts to the named tools.
    """

    os_user: str
    workspace: str
    profile: str
    kind: PrincipalKind = PrincipalKind.HUMAN
    org_id: str = ""
    parent_principal_id: str = ""
    capabilities: Optional[FrozenSet[str]] = None
    credential_handle: str = ""  # keychain handle, never key material

    @property
    def principal_id(self) -> str:
        h = hashlib.sha256()
        h.update(f"{self.os_user}\x00{self.workspace}\x00{self.profile}\x00".encode())
        h.update(f"{self.kind.value}\x00{self.org_id}\x00{self.parent_principal_id}".encode())
        return h.hexdigest()[:32]

    def allows_tool(self, tool_name: str) -> bool:
        return self.capabilities is None or tool_name in self.capabilities


def local_principal(*, workspace: str, profile: str,
                    org_id: str = "") -> Principal:
    """Build the default human principal for this machine/workspace."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    return Principal(os_user=user, workspace=workspace, profile=profile,
                     kind=PrincipalKind.HUMAN, org_id=org_id)


def derive_subagent(parent: Principal,
                    capabilities: FrozenSet[str]) -> Principal:
    """Derive a strictly-narrowed child principal.

    Raises ValueError on any widening attempt (child must be a subset of a
    bounded parent; unbounded parents may bound the child arbitrarily).
    """
    if parent.capabilities is not None and not set(capabilities) <= set(parent.capabilities):
        raise ValueError(
            f"subagent capabilities must narrow the parent: "
            f"{sorted(capabilities)} not subset of {sorted(parent.capabilities)}"
        )
    return Principal(
        os_user=parent.os_user,
        workspace=parent.workspace,
        profile=parent.profile,
        kind=PrincipalKind.SUBAGENT,
        org_id=parent.org_id,
        parent_principal_id=parent.principal_id,
        capabilities=frozenset(capabilities),
    )
