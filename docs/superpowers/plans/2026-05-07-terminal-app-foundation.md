# Terminal App Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first architectural slice of Wisp's Codex-like terminal app by adding provider abstraction, runtime protocol types, a supervisor/state layer, and a minimal Textual TUI entrypoint.

**Architecture:** Keep the current agent loop intact and add new modules around it. The provider layer preserves current `OllamaClient` behavior behind a stable interface, the supervisor owns thread/run metadata and event logs, and the TUI consumes supervisor state without replacing the existing CLI and server paths.

**Tech Stack:** Python 3.10+, sqlite3, Textual, Rich, pytest

---

### Task 1: Provider Foundation

**Files:**
- Create: `wisp/providers/__init__.py`
- Create: `wisp/providers/base.py`
- Create: `wisp/providers/ollama.py`
- Modify: `wisp/config.py`
- Modify: `wisp/core/agent.py`
- Test: `tests/test_config.py`
- Test: `tests/test_provider_factory.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_provider_defaults_to_ollama():
    cfg = WispConfig()
    assert cfg.provider == "ollama"


def test_provider_from_env(monkeypatch):
    monkeypatch.setenv("WISP_PROVIDER", "ollama")
    assert WispConfig().provider == "ollama"


def test_get_provider_returns_ollama_provider():
    cfg = WispConfig()
    provider = get_provider(cfg)
    assert provider.__class__.__name__ == "OllamaProvider"


def test_core_exposes_provider_and_client_alias(monkeypatch):
    cfg = WispConfig()
    cfg.workspace = "/tmp"
    core = WispAgentCore(config=cfg)
    assert core.provider is core.client
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_config.py tests/test_provider_factory.py`
Expected: FAIL because `provider` config and provider factory modules do not exist yet.

- [ ] **Step 3: Implement the minimal provider layer**

```python
# wisp/providers/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional


class BaseProvider(ABC):
    stream_response: Optional[dict] = None

    @abstractmethod
    def check_health(self) -> bool: ...

    @abstractmethod
    def list_models(self) -> list[dict]: ...

    @abstractmethod
    def get_context_length(self) -> int: ...

    @abstractmethod
    def generate(self, system_prompt: str, messages: list[dict], tools: Optional[list] = None) -> dict: ...

    @abstractmethod
    def generate_stream_events(self, system_prompt: str, messages: list[dict], tools: Optional[list] = None, checkpoint_every: int = 50) -> Iterator: ...
```

```python
# wisp/providers/ollama.py
from wisp.ollama_client import OllamaClient
from .base import BaseProvider


class OllamaProvider(OllamaClient, BaseProvider):
    pass
```

```python
# wisp/providers/__init__.py
from .ollama import OllamaProvider


def get_provider(config):
    provider_name = getattr(config, "provider", "ollama")
    if provider_name == "ollama":
        return OllamaProvider(config)
    raise ValueError(f"Unsupported provider: {provider_name}")
```

- [ ] **Step 4: Wire provider config into the agent core**

```python
# config.py
"provider": {
    "type": str,
    "default": "ollama",
    "description": "Model provider backend",
    "env_var": "WISP_PROVIDER",
}
```

```python
# core/agent.py
from wisp.providers import get_provider

self.provider = get_provider(self.config)
self.client = self.provider
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest -q tests/test_config.py tests/test_provider_factory.py`
Expected: PASS


### Task 2: Runtime Protocol Types

**Files:**
- Create: `wisp/runtime_protocol.py`
- Test: `tests/test_runtime_protocol.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_jsonrpc_request_round_trip():
    req = JsonRpcRequest(id="1", method="threads.list", params={"workspace": "/tmp"})
    assert JsonRpcRequest.from_dict(req.to_dict()) == req


def test_app_event_round_trip():
    event = AppEvent(
        event="thread.updated",
        thread_id="thread-1",
        payload={"status": "active"},
    )
    assert AppEvent.from_dict(event.to_dict()) == event
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_runtime_protocol.py`
Expected: FAIL because `wisp/runtime_protocol.py` does not exist yet.

- [ ] **Step 3: Implement the minimal protocol objects**

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class JsonRpcRequest:
    id: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": self.id, "method": self.method, "params": self.params}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JsonRpcRequest":
        return cls(id=str(data["id"]), method=data["method"], params=data.get("params", {}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_runtime_protocol.py`
Expected: PASS


### Task 3: Supervisor and Persistence Slice

**Files:**
- Create: `wisp/persistence/sqlite_store.py`
- Create: `wisp/supervisor.py`
- Test: `tests/test_supervisor.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_supervisor_creates_and_lists_threads(tmp_path):
    store = SQLiteStateStore(tmp_path / "wisp.db")
    supervisor = WispSupervisor(store=store, artifacts_dir=tmp_path / "artifacts")
    thread = supervisor.create_thread(workspace="/tmp/project", title="Project thread")
    threads = supervisor.list_threads()
    assert [t.id for t in threads] == [thread.id]


def test_supervisor_creates_run_and_log_file(tmp_path):
    store = SQLiteStateStore(tmp_path / "wisp.db")
    supervisor = WispSupervisor(store=store, artifacts_dir=tmp_path / "artifacts")
    thread = supervisor.create_thread(workspace="/tmp/project", title="Project thread")
    run = supervisor.start_run(thread.id, "Explain the repo")
    assert run.thread_id == thread.id
    assert supervisor.run_log_path(run.id).parent.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_supervisor.py`
Expected: FAIL because the store and supervisor modules do not exist yet.

- [ ] **Step 3: Implement a minimal SQLite-backed state store**

```python
class SQLiteStateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def create_thread(self, title: str, workspace: str) -> ThreadRecord: ...
    def list_threads(self) -> list[ThreadRecord]: ...
    def create_run(self, thread_id: str, prompt: str, status: str = "queued") -> RunRecord: ...
    def update_run_status(self, run_id: str, status: str) -> None: ...
```

- [ ] **Step 4: Implement the supervisor shell around that store**

```python
class WispSupervisor:
    def __init__(self, store: SQLiteStateStore, artifacts_dir: Path):
        self.store = store
        self.artifacts_dir = Path(artifacts_dir)

    def create_thread(self, workspace: str, title: str | None = None) -> ThreadRecord: ...
    def list_threads(self) -> list[ThreadRecord]: ...
    def start_run(self, thread_id: str, prompt: str) -> RunRecord: ...
    def run_log_path(self, run_id: str) -> Path: ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest -q tests/test_supervisor.py`
Expected: PASS


### Task 4: Minimal Textual TUI Shell and CLI Wiring

**Files:**
- Create: `wisp/tui/__init__.py`
- Create: `wisp/tui/app.py`
- Modify: `wisp/__main__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_tui_app.py`
- Test: `tests/test_commands.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_tui_app_constructs_with_supervisor(tmp_path):
    store = SQLiteStateStore(tmp_path / "wisp.db")
    supervisor = WispSupervisor(store=store, artifacts_dir=tmp_path / "artifacts")
    app = WispTUIApp(config=WispConfig(), supervisor=supervisor)
    assert app.title == "Wisp Terminal App"


def test_cmd_tui_runs_app(monkeypatch):
    called = {}

    class FakeApp:
        def run(self):
            called["ran"] = True

    monkeypatch.setattr("wisp.tui.app.WispTUIApp", FakeApp)
    cmd_tui()
    assert called["ran"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_tui_app.py tests/test_commands.py`
Expected: FAIL because the TUI module, dependency, and command do not exist yet.

- [ ] **Step 3: Add the Textual dependency and minimal app**

```toml
dependencies = [
    "requests>=2.28",
    "pyyaml>=6.0",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "python-multipart>=0.0.9",
    "websockets>=12.0",
    "textual>=0.61",
]
```

```python
class WispTUIApp(App):
    TITLE = "Wisp Terminal App"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
```

- [ ] **Step 4: Wire the CLI entrypoint**

```python
def cmd_tui(model=None, workspace=None, auto_approve=False, show_thinking=False):
    config = WispConfig()
    ...
    app = WispTUIApp(config=config)
    app.run()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest -q tests/test_tui_app.py tests/test_commands.py`
Expected: PASS


### Task 5: Focused Verification

**Files:**
- Modify: `docs/superpowers/plans/2026-05-07-terminal-app-foundation.md`

- [ ] **Step 1: Run the focused test set**

Run: `pytest -q tests/test_config.py tests/test_provider_factory.py tests/test_runtime_protocol.py tests/test_supervisor.py tests/test_tui_app.py tests/test_commands.py`
Expected: PASS

- [ ] **Step 2: Run one smoke command**

Run: `python -m wisp --help`
Expected: includes `tui`

- [ ] **Step 3: Run a TUI import smoke test**

Run: `python - <<'PY'\nfrom wisp.tui.app import WispTUIApp\nprint(WispTUIApp.TITLE)\nPY`
Expected: prints `Wisp Terminal App`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml wisp/config.py wisp/core/agent.py wisp/providers wisp/runtime_protocol.py wisp/persistence wisp/supervisor.py wisp/tui tests/test_config.py tests/test_provider_factory.py tests/test_runtime_protocol.py tests/test_supervisor.py tests/test_tui_app.py docs/superpowers/plans/2026-05-07-terminal-app-foundation.md
git commit -m "feat: add terminal app foundation"
```
