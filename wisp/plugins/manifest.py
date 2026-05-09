"""Plugin manifest format — the plugin.json schema for Wisp plugins."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PluginCommand:
    """A slash command provided by a plugin."""

    name: str
    description: str
    handler: str
    arguments: list[dict] = field(default_factory=list)


@dataclass
class PluginManifest:
    """A plugin's metadata and capabilities, loaded from plugin.json."""

    name: str
    version: str
    description: str
    author: str
    license: str
    namespace: str

    skills: list[str] = field(default_factory=list)
    commands: list[PluginCommand] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    mcp_servers: list[dict] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)

    requires_wisp_version: str = ">=0.1.0"
    plugin_dependencies: dict[str, str] = field(default_factory=dict)

    homepage: str | None = None
    repository: str | None = None

    # Internal — not serialized
    _source_path: Path | None = field(default=None, repr=False, compare=False)

    @staticmethod
    def from_file(path: Path) -> PluginManifest:
        """Load a PluginManifest from a plugin.json file.

        Args:
            path: Path to the plugin.json file.

        Returns:
            A populated PluginManifest instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the manifest is missing required fields or has
                        invalid data.
            json.JSONDecodeError: If the file contains invalid JSON.
        """
        if not path.exists():
            raise FileNotFoundError(f"Plugin manifest not found: {path}")

        raw = json.loads(path.read_text(encoding="utf-8"))

        # ── required fields ─────────────────────────────────────────
        required = ["name", "version", "description", "author", "license", "namespace"]
        missing = [k for k in required if k not in raw]
        if missing:
            raise ValueError(
                f"Plugin manifest {path} missing required fields: {missing}"
            )

        # ── parse commands ───────────────────────────────────────────
        commands: list[PluginCommand] = []
        for cmd_data in raw.get("commands", []):
            commands.append(
                PluginCommand(
                    name=cmd_data["name"],
                    description=cmd_data.get("description", ""),
                    handler=cmd_data["handler"],
                    arguments=cmd_data.get("arguments", []),
                )
            )

        # ── build manifest ───────────────────────────────────────────
        manifest = PluginManifest(
            name=raw["name"],
            version=raw["version"],
            description=raw["description"],
            author=raw["author"],
            license=raw["license"],
            namespace=raw["namespace"],
            skills=raw.get("skills", []),
            commands=commands,
            hooks=raw.get("hooks", []),
            mcp_servers=raw.get("mcp_servers", []),
            agents=raw.get("agents", []),
            themes=raw.get("themes", []),
            requires_wisp_version=raw.get("requires_wisp_version", ">=0.1.0"),
            plugin_dependencies=raw.get("plugin_dependencies", {}),
            homepage=raw.get("homepage"),
            repository=raw.get("repository"),
            _source_path=path,
        )

        logger.debug("Loaded plugin manifest for %s v%s", manifest.name, manifest.version)
        return manifest

    def to_dict(self) -> dict:
        """Serialize back to a plugin.json-compatible dictionary.

        Internal fields (_source_path) are excluded.
        """
        data: dict = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "license": self.license,
            "namespace": self.namespace,
            "skills": self.skills,
            "commands": [
                {
                    "name": c.name,
                    "description": c.description,
                    "handler": c.handler,
                    "arguments": c.arguments,
                }
                for c in self.commands
            ],
            "hooks": self.hooks,
            "mcp_servers": self.mcp_servers,
            "agents": self.agents,
            "themes": self.themes,
            "requires_wisp_version": self.requires_wisp_version,
            "plugin_dependencies": self.plugin_dependencies,
        }
        if self.homepage is not None:
            data["homepage"] = self.homepage
        if self.repository is not None:
            data["repository"] = self.repository
        return data

    def validate(self) -> list[str]:
        """Validate the manifest for correctness.

        Returns a list of error messages (empty if valid).
        """
        errors: list[str] = []

        if not self.name or not self.name.strip():
            errors.append("name is required and must be non-empty")
        elif not self.name.replace("-", "").replace("_", "").replace(".", "").isalnum():
            errors.append(
                f"name '{self.name}' contains invalid characters "
                "(only a-z, 0-9, -, _, . allowed)"
            )

        if not self.version:
            errors.append("version is required")
        else:
            parts = self.version.split(".")
            if len(parts) < 2 or len(parts) > 3:
                errors.append(f"version '{self.version}' is not valid semver")
            else:
                for part in parts:
                    if not part.isdigit():
                        errors.append(f"version '{self.version}' is not valid semver")
                        break

        if not self.namespace or not self.namespace.strip():
            errors.append("namespace is required and must be non-empty")
        elif not self.namespace.isidentifier():
            errors.append(
                f"namespace '{self.namespace}' is not a valid Python identifier"
            )

        for i, cmd in enumerate(self.commands):
            if not cmd.name:
                errors.append(f"commands[{i}]: name is required")
            if not cmd.handler:
                errors.append(f"commands[{i}]: handler path is required")

        return errors
