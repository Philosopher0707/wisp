"""Plugin + MCP manifest wire schemas (M1a). Mirror existing fields exactly;
enterprise extensions (origin/scopes/signature) are reserved-optional,
unpopulated until Phase 3 defines the bundle format."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class PluginContract:
    # Mirrors wisp/plugins/manifest.py required + capability fields.
    name: str
    version: str
    description: str
    author: str
    license: str
    namespace: str
    skills: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    hooks: tuple[str, ...] = ()
    mcp_servers: tuple[dict[str, Any], ...] = ()
    agents: tuple[str, ...] = ()
    themes: tuple[str, ...] = ()
    requires_wisp_version: str = ">=0.1.0"
    plugin_dependencies: dict[str, str] = field(default_factory=dict)
    homepage: Optional[str] = None
    repository: Optional[str] = None
    # Reserved enterprise extensions (Phase 3).
    origin: Optional[str] = None
    scopes: tuple[str, ...] = ()
    signature: Optional[str] = None
    contract_version: int = 1

    @classmethod
    def from_plugin_manifest(cls, m: Any) -> "PluginContract":
        cmds = tuple(c["name"] if isinstance(c, dict) else getattr(c, "name", c)
                     for c in (m.commands or []))
        return cls(name=m.name, version=m.version, description=m.description,
                   author=m.author, license=m.license, namespace=m.namespace,
                   skills=tuple(m.skills or []), commands=cmds,
                   hooks=tuple(m.hooks or []),
                   mcp_servers=tuple(dict(s) if isinstance(s, dict) else s
                                     for s in (m.mcp_servers or [])),
                   agents=tuple(m.agents or []), themes=tuple(m.themes or []),
                   requires_wisp_version=m.requires_wisp_version,
                   plugin_dependencies=dict(m.plugin_dependencies or {}),
                   homepage=m.homepage, repository=m.repository)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version,
                "description": self.description, "author": self.author,
                "license": self.license, "namespace": self.namespace,
                "skills": list(self.skills), "commands": list(self.commands),
                "hooks": list(self.hooks),
                "mcp_servers": list(self.mcp_servers),
                "agents": list(self.agents), "themes": list(self.themes),
                "requires_wisp_version": self.requires_wisp_version,
                "plugin_dependencies": self.plugin_dependencies,
                "homepage": self.homepage, "repository": self.repository,
                "origin": self.origin, "scopes": list(self.scopes),
                "signature": self.signature,
                "contract_version": self.contract_version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PluginContract":
        known = {"name", "version", "description", "author", "license",
                 "namespace", "skills", "commands", "hooks", "mcp_servers",
                 "agents", "themes", "requires_wisp_version",
                 "plugin_dependencies", "homepage", "repository", "origin",
                 "scopes", "signature", "contract_version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown plugin fields: {sorted(unknown)}")
        for k in ("name", "version", "description", "author", "license", "namespace"):
            if k not in d:
                raise ValueError(f"plugin missing required field: {k}")
        d = dict(d)
        # to_dict emits lists; normalize back to tuples so
        # from_dict(to_dict(x)) == x holds.
        for k in ("skills", "commands", "hooks", "agents", "themes", "scopes"):
            d[k] = tuple(d.get(k) or [])
        d["mcp_servers"] = tuple(d.get("mcp_servers") or [])
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass(frozen=True)
class MCPServerContract:
    # Mirrors all 13 wisp/mcp/manager.py:73-86 MCPServerConfig fields.
    name: str
    command: Optional[str] = None
    args: tuple[str, ...] = ()
    url: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    transport: str = "stdio"
    always_load: bool = False
    auth: str = "none"  # str(auth enum); never leak credential material
    auth_config: Optional[dict[str, Any]] = None
    timeout_seconds: int = 30
    headers: Optional[dict[str, str]] = None
    disabled_tools: Optional[tuple[str, ...]] = None
    # Reserved enterprise extensions (Phase 3).
    origin: Optional[str] = None
    scopes: tuple[str, ...] = ()
    signature: Optional[str] = None
    contract_version: int = 1

    @classmethod
    def from_server_config(cls, c: Any) -> "MCPServerContract":
        return cls(name=c.name, command=c.command, args=tuple(c.args or []),
                   url=c.url, env=dict(c.env or {}), disabled=c.disabled,
                   transport=c.transport, always_load=c.always_load,
                   auth=str(getattr(c.auth, "value", c.auth)),
                   auth_config=(dict(c.auth_config) if c.auth_config else None),
                   timeout_seconds=c.timeout_seconds,
                   headers=(dict(c.headers) if c.headers else None),
                   disabled_tools=(tuple(c.disabled_tools)
                                   if c.disabled_tools else None))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "command": self.command,
                "args": list(self.args), "url": self.url, "env": self.env,
                "disabled": self.disabled, "transport": self.transport,
                "always_load": self.always_load, "auth": self.auth,
                "auth_config": self.auth_config,
                "timeout_seconds": self.timeout_seconds,
                "headers": self.headers,
                "disabled_tools": (list(self.disabled_tools)
                                   if self.disabled_tools is not None else None),
                "origin": self.origin, "scopes": list(self.scopes),
                "signature": self.signature,
                "contract_version": self.contract_version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPServerContract":
        known = {"name", "command", "args", "url", "env", "disabled",
                 "transport", "always_load", "auth", "auth_config",
                 "timeout_seconds", "headers", "disabled_tools", "origin",
                 "scopes", "signature", "contract_version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown mcp fields: {sorted(unknown)}")
        if "name" not in d:
            raise ValueError("mcp missing required field: name")
        d = dict(d)
        d["args"] = tuple(d.get("args") or [])
        d["env"] = dict(d.get("env") or {})
        d["scopes"] = tuple(d.get("scopes") or [])
        if d.get("disabled_tools") is not None:
            d["disabled_tools"] = tuple(d["disabled_tools"])
        return cls(**{k: v for k, v in d.items() if k in known})
