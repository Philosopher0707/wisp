"""Reproducibility manifest (M3, pure). Every task output preserves the
versions and hashes needed to reconstruct it: model/provider, policy
bundle, tool/plugin/MCP manifests, workspace commit + diff, I/O hashes.
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReproManifest:
    wisp_version: str = ""
    model: str = ""
    provider: str = ""
    policy_bundle_id: str = ""
    tool_versions: dict[str, str] = field(default_factory=dict)
    plugin_versions: dict[str, str] = field(default_factory=dict)
    mcp_versions: dict[str, str] = field(default_factory=dict)
    workspace_commit: str = ""
    workspace_diff_hash: str = ""
    input_hash: str = ""
    output_hash: str = ""
    version: int = 1

    def to_dict(self) -> dict:
        return {"wisp_version": self.wisp_version, "model": self.model,
                "provider": self.provider,
                "policy_bundle_id": self.policy_bundle_id,
                "tool_versions": self.tool_versions,
                "plugin_versions": self.plugin_versions,
                "mcp_versions": self.mcp_versions,
                "workspace_commit": self.workspace_commit,
                "workspace_diff_hash": self.workspace_diff_hash,
                "input_hash": self.input_hash, "output_hash": self.output_hash,
                "version": self.version}

    @classmethod
    def from_dict(cls, d: dict) -> "ReproManifest":
        known = {"wisp_version", "model", "provider", "policy_bundle_id",
                 "tool_versions", "plugin_versions", "mcp_versions",
                 "workspace_commit", "workspace_diff_hash", "input_hash",
                 "output_hash", "version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown repro fields: {sorted(unknown)}")
        return cls(**{k: v for k, v in d.items() if k in known})

    def manifest_hash(self) -> str:
        """Stable content hash (excludes nothing — the manifest is data)."""
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:32]
