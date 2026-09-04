# M1a Contracts Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the additive `wisp/contracts/` package (5 envelopes + adapters) with fixture-based compat tests, changing zero existing behavior.

**Architecture:** Frozen dataclasses mirroring existing source types (`AgentEvent`, `ApprovalDecision`, `infra PolicyDecision`, `PluginManifest`, `MCPServerConfig`); strict constructors (dataclass `__init__` rejects unknown kwargs natively — no extra code); `from_*` constructors bridge existing types; adapters convert nested↔flat at the transport edge.

**Tech Stack:** Python 3.12+, stdlib only (`dataclasses`, `json`, `time`), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-04-enterprise-contracts-m1a-design.md` (rev 3, approved).

---

## Chunk 1: Event envelope + tool envelopes + fixtures

### Task 1: `wisp/contracts/__init__.py` + `envelope.py`

**Files:**
- Create: `wisp/contracts/__init__.py`
- Create: `wisp/contracts/envelope.py`
- Test: `tests/test_contracts_envelope.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_envelope.py
from wisp.contracts import CONTRACT_VERSION, CanonicalEvent
from wisp.core.events import AgentEvent


def test_version_constant():
    assert CONTRACT_VERSION == 1


def test_from_agent_event_round_trip():
    ev = AgentEvent(type="content", data={"text": "hi"},
                    trace_id="t1", span_id="s1")
    c = CanonicalEvent.from_agent_event(ev)
    assert c.schema_version == 1
    back = c.to_agent_event()
    assert back.type == "content" and back.trace_id == "t1"


def test_unknown_field_rejected():
    import pytest
    with pytest.raises(TypeError):
        CanonicalEvent(type="content", data={}, bogus=1)


def test_from_dict_unknown_field_rejected():
    import pytest
    with pytest.raises(ValueError, match="unknown envelope fields"):
        CanonicalEvent.from_dict({"type": "content", "data": {}, "bogus": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_contracts_envelope.py -q`
Expected: FAIL with "No module named wisp.contracts"

- [ ] **Step 3: Write minimal implementation**

```python
# wisp/contracts/envelope.py
"""Canonical nested event envelope (M1a contract freeze)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from wisp.core.events import AgentEvent

CONTRACT_VERSION = 1


@dataclass(frozen=True)
class CanonicalEvent:
    """Nested-only canonical event. Field `schema_version` matches
    AgentEvent.to_dict() wire name exactly (no rename on the wire)."""
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    trace_id: str = ""
    span_id: str = ""
    schema_version: int = CONTRACT_VERSION

    @classmethod
    def from_agent_event(cls, ev: AgentEvent) -> "CanonicalEvent":
        return cls(type=str(ev.type), data=dict(ev.data),
                   timestamp=ev.timestamp, trace_id=ev.trace_id,
                   span_id=ev.span_id, schema_version=ev.schema_version)

    def to_agent_event(self) -> AgentEvent:
        return AgentEvent(type=self.type, data=dict(self.data),
                          timestamp=self.timestamp, trace_id=self.trace_id,
                          span_id=self.span_id, schema_version=self.schema_version)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "data": self.data,
                             "timestamp": self.timestamp,
                             "schema_version": self.schema_version}
        if self.trace_id:
            d["trace_id"] = self.trace_id
        if self.span_id:
            d["span_id"] = self.span_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CanonicalEvent":
        known = {"type", "data", "timestamp", "trace_id", "span_id", "schema_version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown envelope fields: {sorted(unknown)}")
        return cls(type=d.get("type", ""), data=dict(d.get("data") or {}),
                   timestamp=d.get("timestamp", 0.0),
                   trace_id=d.get("trace_id", ""), span_id=d.get("span_id", ""),
                   schema_version=d.get("schema_version", CONTRACT_VERSION))
```

```python
# wisp/contracts/__init__.py
from wisp.contracts.envelope import CONTRACT_VERSION, CanonicalEvent

__all__ = ["CONTRACT_VERSION", "CanonicalEvent"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_contracts_envelope.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add wisp/contracts/__init__.py wisp/contracts/envelope.py tests/test_contracts_envelope.py
git commit -m "feat(contracts): canonical nested event envelope (M1a)"
```

### Task 2: `tool.py` (ToolRequest/ToolResult)

**Files:**
- Create: `wisp/contracts/tool.py`
- Test: `tests/test_contracts_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_tool.py
import pytest
from wisp.contracts.tool import ToolRequest, ToolResult, BLOCK_REASONS


def test_request_round_trip():
    r = ToolRequest(tool_call_id="c1", name="read_file",
                    args={"path": "a.py"}, idempotency_key="k1")
    assert ToolRequest.from_dict(r.to_dict()) == r
    assert r.version == 1


def test_result_denied_carries_block_reason():
    res = ToolResult(tool_call_id="c1", status="denied",
                     block_reason="danger", error="rm -rf / blocked")
    assert res.to_dict()["block_reason"] == "danger"


def test_bad_status_rejected():
    with pytest.raises(ValueError):
        ToolResult(tool_call_id="c1", status="maybe")


def test_unknown_field_rejected():
    with pytest.raises(TypeError):
        ToolRequest(tool_call_id="c", name="n", args={}, bogus=1)


def test_from_dict_unknown_fields_rejected():
    with pytest.raises(ValueError, match="unknown Tool"):
        ToolRequest.from_dict({"tool_call_id": "c", "name": "n", "bogus": 1})
    with pytest.raises(ValueError, match="unknown Tool"):
        ToolResult.from_dict({"tool_call_id": "c", "status": "ok", "bogus": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_contracts_tool.py -q`
Expected: FAIL with "No module named wisp.contracts.tool"

- [ ] **Step 3: Write minimal implementation**

```python
# wisp/contracts/tool.py
"""Tool-request/result wire envelopes (M1a contract freeze)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

TOOL_VERSION = 1
STATUSES = ("ok", "error", "denied", "cancelled")
# Canonicalization of wisp/tool_executor.py:449-481 block branches.
# "" is the "no block" sentinel (status ok/error carry no block reason).
# Out of scope: pre_bash/pre_file hooks (:527-536) fold into "pre_tool";
# approval-decline folds into "permission".
BLOCK_REASONS = ("repeat_guard", "pre_tool", "plan", "danger", "permission", "")


@dataclass(frozen=True)
class ToolRequest:
    tool_call_id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    principal_id: str = ""      # reserved, supplier TBD Phase 1
    correlation_id: str = ""    # reserved, supplier TBD Phase 1
    idempotency_key: str = ""
    version: int = TOOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"tool_call_id": self.tool_call_id, "name": self.name,
                "args": self.args, "principal_id": self.principal_id,
                "correlation_id": self.correlation_id,
                "idempotency_key": self.idempotency_key, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolRequest":
        known = {"tool_call_id", "name", "args", "principal_id",
                 "correlation_id", "idempotency_key", "version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown ToolRequest fields: {sorted(unknown)}")
        return cls(tool_call_id=d["tool_call_id"], name=d["name"],
                   args=dict(d.get("args") or {}),
                   principal_id=d.get("principal_id", ""),
                   correlation_id=d.get("correlation_id", ""),
                   idempotency_key=d.get("idempotency_key", ""),
                   version=d.get("version", TOOL_VERSION))


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    status: str  # one of STATUSES; mirrors {status,data,metadata} wrapper
    data: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    auto_approved: bool = False
    block_reason: str = ""  # one of BLOCK_REASONS
    version: int = TOOL_VERSION

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"bad status: {self.status!r}")
        if self.block_reason not in BLOCK_REASONS:
            raise ValueError(f"bad block_reason: {self.block_reason!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"tool_call_id": self.tool_call_id, "status": self.status,
                "data": self.data, "metadata": self.metadata, "error": self.error,
                "auto_approved": self.auto_approved,
                "block_reason": self.block_reason, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolResult":
        known = {"tool_call_id", "status", "data", "metadata", "error",
                 "auto_approved", "block_reason", "version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown ToolResult fields: {sorted(unknown)}")
        return cls(tool_call_id=d["tool_call_id"], status=d["status"],
                   data=d.get("data"), metadata=dict(d.get("metadata") or {}),
                   error=d.get("error", ""), auto_approved=d.get("auto_approved", False),
                   block_reason=d.get("block_reason", ""),
                   version=d.get("version", TOOL_VERSION))
```

Also append `ToolRequest, ToolResult` to `wisp/contracts/__init__.py` imports/`__all__`.

- [ ] **Step 4: Run both test files**

Run: `python3 -m pytest tests/test_contracts_envelope.py tests/test_contracts_tool.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add wisp/contracts/tool.py wisp/contracts/__init__.py tests/test_contracts_tool.py
git commit -m "feat(contracts): tool request/result envelopes (M1a)"
```

---

## Chunk 2: Policy envelope + run vocabulary + manifests

### Task 3: `policy.py` (wire envelope over two existing decision types)

**Files:**
- Create: `wisp/contracts/policy.py`
- Test: `tests/test_contracts_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_policy.py
from wisp.contracts.policy import PolicyDecisionEnvelope, CANCELLED_BY_USER
from wisp.core.contracts import ApprovalDecision, ToolRisk
from wisp.infra.policy_engine import PolicyDecision


def test_from_gate_decision():
    env = PolicyDecisionEnvelope.from_gate_decision(
        ApprovalDecision(allowed=False, reason="nope", risk=ToolRisk.EXEC))
    assert env.allowed is False and env.risk == "exec"


def test_from_engine_decision():
    env = PolicyDecisionEnvelope.from_engine_decision(
        PolicyDecision.deny("r1", "bad"))
    assert env.rule_name == "r1" and env.allowed is False


def test_cancel_is_denial_not_exception():
    env = PolicyDecisionEnvelope.cancelled("c1")
    assert env.allowed is False and env.reason == CANCELLED_BY_USER
    assert PolicyDecisionEnvelope.from_dict(env.to_dict()) == env


def test_from_dict_unknown_field_rejected():
    import pytest
    with pytest.raises(ValueError, match="unknown policy fields"):
        PolicyDecisionEnvelope.from_dict({"allowed": True, "bogus": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_contracts_policy.py -q`
Expected: FAIL with "No module named wisp.contracts.policy"

- [ ] **Step 3: Write minimal implementation**

```python
# wisp/contracts/policy.py
"""Policy-decision wire envelope (M1a). Adds no authority: serializes the two
existing decision types (core/contracts.ApprovalDecision,
infra/policy_engine.PolicyDecision) into one wire form."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

CANCELLED_BY_USER = "cancelled_by_user"


@dataclass(frozen=True)
class PolicyDecisionEnvelope:
    allowed: bool
    reason: str = ""
    modified_args: Optional[dict[str, Any]] = None
    rule_name: str = ""
    risk: str = "read"
    controlling_layer: str = ""  # reserved: built-in/org/admin/workspace/session
    principal_id: str = ""       # reserved, supplier TBD Phase 1
    correlation_id: str = ""     # reserved, supplier TBD Phase 1
    version: int = 1

    @classmethod
    def from_gate_decision(cls, d: Any) -> "PolicyDecisionEnvelope":
        return cls(allowed=d.allowed, reason=d.reason,
                   modified_args=d.modified_args, risk=str(getattr(d.risk, "value", d.risk)))

    @classmethod
    def from_engine_decision(cls, d: Any) -> "PolicyDecisionEnvelope":
        # Explicit exclusion (spec §3): engine decisions carry no risk, so the
        # wire form defaults to "read". Callers with risk knowledge must set
        # it explicitly; never infer authority from a default.
        return cls(allowed=d.allowed, reason=d.reason,
                   modified_args=d.modified_args, rule_name=d.rule_name)

    @classmethod
    def cancelled(cls, correlation_id: str = "") -> "PolicyDecisionEnvelope":
        return cls(allowed=False, reason=CANCELLED_BY_USER,
                   correlation_id=correlation_id)

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason,
                "modified_args": self.modified_args, "rule_name": self.rule_name,
                "risk": self.risk, "controlling_layer": self.controlling_layer,
                "principal_id": self.principal_id,
                "correlation_id": self.correlation_id, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PolicyDecisionEnvelope":
        known = {"allowed", "reason", "modified_args", "rule_name", "risk",
                 "controlling_layer", "principal_id", "correlation_id", "version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown policy fields: {sorted(unknown)}")
        return cls(allowed=d["allowed"], reason=d.get("reason", ""),
                   modified_args=d.get("modified_args"),
                   rule_name=d.get("rule_name", ""), risk=d.get("risk", "read"),
                   controlling_layer=d.get("controlling_layer", ""),
                   principal_id=d.get("principal_id", ""),
                   correlation_id=d.get("correlation_id", ""),
                   version=d.get("version", 1))
```

Update `__init__.py`. Note: `Any`-typed params avoid importing core/infra at module load (no coupling); tests prove the bridge.

- [ ] **Step 4: Run test**

Run: `python3 -m pytest tests/test_contracts_policy.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add wisp/contracts/policy.py wisp/contracts/__init__.py tests/test_contracts_policy.py
git commit -m "feat(contracts): policy decision wire envelope (M1a)"
```

### Task 4: `run.py` (produced run vocabulary only)

**Files:**
- Create: `wisp/contracts/run.py`
- Test: `tests/test_contracts_run.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_run.py
from wisp.contracts.run import RunStatus, EVENT_KINDS, Transition
from wisp.multi_agent.task import EventKind


def test_run_status_is_produced_vocabulary():
    assert {s.value for s in RunStatus} == {"running", "completed", "failed", "cancelled"}


def test_event_kinds_match_producers():
    assert set(EVENT_KINDS) == {EventKind.PLANNING, EventKind.TASK_STARTED,
        EventKind.TASK_PROGRESS, EventKind.TASK_COMPLETED, EventKind.TASK_FAILED,
        EventKind.TASK_RETRY, EventKind.DONE}


def test_transition_round_trip():
    t = Transition(run_id="r1", seq=0, from_state="running", to_state="completed")
    assert Transition.from_dict(t.to_dict()) == t


def test_from_dict_unknown_field_rejected():
    import pytest
    with pytest.raises(ValueError, match="unknown transition fields"):
        Transition.from_dict({"run_id": "r", "seq": 0,
                              "from_state": "a", "to_state": "b", "bogus": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_contracts_run.py -q`
Expected: FAIL with "No module named wisp.contracts.run"

- [ ] **Step 3: Write minimal implementation**

```python
# wisp/contracts/run.py
"""Produced run vocabulary freeze (M1a). The 8-state lifecycle is M1b design;
this module pins only what producers emit today."""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum

# Exact wire values from wisp/multi_agent/task.py:243-251 (no rename).
EVENT_KINDS: tuple[str, ...] = ("planning", "task_started", "task_progress",
    "task_completed", "task_failed", "task_retry", "done")


class RunStatus(StrEnum):
    """Background-agent statuses, exactly as produced
    (wisp/multi_agent/background.py)."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Transition:
    """One append-only run-state transition record."""
    run_id: str
    seq: int
    from_state: str
    to_state: str
    reason: str = ""
    timestamp: float = 0.0
    version: int = 1

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "seq": self.seq,
                "from_state": self.from_state, "to_state": self.to_state,
                "reason": self.reason, "timestamp": self.timestamp,
                "version": self.version}

    @classmethod
    def from_dict(cls, d: dict) -> "Transition":
        known = {"run_id", "seq", "from_state", "to_state", "reason",
                 "timestamp", "version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown transition fields: {sorted(unknown)}")
        return cls(run_id=d["run_id"], seq=d["seq"],
                   from_state=d["from_state"], to_state=d["to_state"],
                   reason=d.get("reason", ""), timestamp=d.get("timestamp", 0.0),
                   version=d.get("version", 1))
```

Update `__init__.py`.

- [ ] **Step 4: Run test**

Run: `python3 -m pytest tests/test_contracts_run.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add wisp/contracts/run.py wisp/contracts/__init__.py tests/test_contracts_run.py
git commit -m "feat(contracts): produced run vocabulary freeze (M1a)"
```

### Task 5: `manifest.py` (mirrored fields + reserved extensions)

**Files:**
- Create: `wisp/contracts/manifest.py`
- Test: `tests/test_contracts_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_manifest.py
from pathlib import Path
import json
from wisp.contracts.manifest import PluginContract, MCPServerContract


def test_plugin_mirror(tmp_path):
    from wisp.plugins.manifest import PluginManifest
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps({"name": "x", "version": "1.0", "description": "d",
        "author": "a", "license": "MIT", "namespace": "n",
        "commands": [{"name": "c1", "description": "c", "handler": "h"}]}))
    m = PluginManifest.from_file(p)
    c = PluginContract.from_plugin_manifest(m)
    assert c.commands == ("c1",)  # exercises PluginCommand→name extraction
    assert c.name == "x" and c.signature is None  # reserved, unpopulated
    assert PluginContract.from_dict(c.to_dict()) == c


def test_mcp_mirror():
    from wisp.mcp.manager import MCPServerConfig
    cfg = MCPServerConfig(name="s", command="uvx", args=["mcp"])
    c = MCPServerContract.from_server_config(cfg)
    assert c.transport == "stdio" and c.origin is None
    assert MCPServerContract.from_dict(c.to_dict()) == c


def test_from_dict_unknown_fields_rejected():
    import pytest
    with pytest.raises(ValueError, match="unknown plugin fields"):
        PluginContract.from_dict({"name": "x", "version": "1", "description": "d",
            "author": "a", "license": "M", "namespace": "n", "bogus": 1})
    with pytest.raises(ValueError, match="unknown mcp fields"):
        MCPServerContract.from_dict({"name": "s", "bogus": 1})


def test_from_dict_requires_name():
    import pytest
    with pytest.raises(ValueError, match="required field"):
        PluginContract.from_dict({"version": "1"})
    with pytest.raises(ValueError, match="required field"):
        MCPServerContract.from_dict({})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_contracts_manifest.py -q`
Expected: FAIL with "No module named wisp.contracts.manifest"

- [ ] **Step 3: Write minimal implementation**

```python
# wisp/contracts/manifest.py
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
```

```python
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
```

- [ ] **Step 4: Run test**

Run: `python3 -m pytest tests/test_contracts_manifest.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add wisp/contracts/manifest.py wisp/contracts/__init__.py tests/test_contracts_manifest.py
git commit -m "feat(contracts): plugin+MCP manifest schemas (M1a)"
```

---

## Chunk 3: Adapters + goldens + gate verification

### Task 6: `adapters.py` + fixtures + transport goldens

**Files:**
- Create: `wisp/contracts/adapters.py`
- Create: `tests/fixtures/contracts/*.json` (8 goldens)
- Test: `tests/test_contracts_adapters.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_adapters.py
import json
from pathlib import Path
from wisp.contracts.adapters import (to_flat, from_flat, for_cli, for_tui,
                                      for_websocket, for_headless)
from wisp.contracts import CanonicalEvent

FIX = Path(__file__).resolve().parent / "fixtures" / "contracts"


def test_aliases_share_implementation():
    assert for_cli is to_flat and for_tui is to_flat
    assert for_websocket is to_flat and for_headless is to_flat


def test_flat_golden_byte_stable():
    flat = json.loads((FIX / "event_flat.json").read_text())
    ev = CanonicalEvent.from_dict(json.loads((FIX / "event_nested.json").read_text()))
    assert to_flat(ev) == flat  # byte-stability: flat consumers keep working


def test_from_flat_lenient_folds_unknowns():
    ev = from_flat({"type": "content", "text": "hi", "future": "x"})
    assert ev.data == {"text": "hi", "future": "x"}  # matches AgentEvent.from_dict


def test_nested_round_trip_lossless():
    for p in sorted(FIX.glob("nested_*.json")):
        d = json.loads(p.read_text())
        assert CanonicalEvent.from_dict(d).to_dict() == d
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_contracts_adapters.py -q`
Expected: FAIL with "No module named wisp.contracts.adapters"

- [ ] **Step 3: Write implementation + fixtures**

```python
# wisp/contracts/adapters.py
"""Edge adapters: canonical nested <-> transport flat shapes (M1a).

`to_flat` mirrors the three existing flatten sites
(core/stateless.py:105, core/provider_stream.py, transport/headless.py)
without touching them; `from_flat` is lenient like AgentEvent.from_dict."""
from __future__ import annotations
from typing import Any

from wisp.contracts.envelope import CanonicalEvent


def to_flat(ev: CanonicalEvent) -> dict[str, Any]:
    # Named exclusion (spec §3): mirrors the three existing flatten sites by
    # dropping trace context, so flat consumers see byte-identical shapes.
    # Lineage preservation lives in the nested form (to_dict/from_dict).
    flat: dict[str, Any] = dict(ev.data)
    flat["type"] = ev.type
    flat["timestamp"] = ev.timestamp
    return flat


def from_flat(d: dict[str, Any]) -> CanonicalEvent:
    # Lenient like AgentEvent.from_dict: known envelope keys become fields,
    # everything else folds into data.
    data = {k: v for k, v in d.items()
            if k not in ("type", "timestamp", "trace_id", "span_id", "schema_version")}
    return CanonicalEvent(type=d.get("type", ""), data=data,
                          timestamp=d.get("timestamp", 0.0),
                          trace_id=d.get("trace_id", ""),
                          span_id=d.get("span_id", ""),
                          schema_version=d.get("schema_version", 1))


for_cli = to_flat
for_tui = to_flat
for_websocket = to_flat
for_headless = to_flat
```

Fixtures (`event_nested.json` must satisfy nested round-trip exactly —
note `to_dict` omits empty trace context, so either include non-empty
trace IDs or accept their absence; `event_flat.json` is `to_flat` output):

```jsonc
// event_nested.json
{"type": "content", "data": {"text": "hi"}, "timestamp": 1.5,
 "trace_id": "t1", "span_id": "s1", "schema_version": 1}
// event_flat.json
{"text": "hi", "type": "content", "timestamp": 1.5}
```

Plus `tool_request.json`, `tool_result_denied.json`, `policy_cancel.json`,
`run_transition.json`, `plugin_manifest.json`, `mcp_manifest.json` — each the
exact `to_dict()` output of a representative instance (generate with
`python3 -c`, never hand-write, then freeze).

- [ ] **Step 4: Run full contracts suite + ruff**

Run: `python3 -m pytest tests/test_contracts_*.py -q` Expected: all pass
Run: `python3 -m ruff check wisp/contracts tests/test_contracts_*.py` Expected: clean

- [ ] **Step 5: Commit**

```bash
git add wisp/contracts/adapters.py wisp/contracts/__init__.py tests/test_contracts_adapters.py tests/fixtures/contracts/
git commit -m "feat(contracts): edge adapters + golden fixtures (M1a)"
```

### Task 7: Gate verification (no regressions, spec acceptance)

- [ ] **Step 1: Run neighboring suites**

Run: `python3 -m pytest tests/test_contracts_*.py tests/test_approval_loop.py tests/test_core_stateless.py tests/test_transport_cli.py -q -p no:randomly`
Expected: all pass (additive package must break nothing)

- [ ] **Step 2: Confirm acceptance mapping**

  - Same canonical events on all paths: 4 aliases + per-transport goldens ✅
  - No-data-loss migration: additive-only + byte-stability test ✅
  - Version + fixtures per contract: 5 envelopes × (`version` field + JSON golden) ✅

- [ ] **Step 3: Final commit if needed (docs touch-ups only)**
