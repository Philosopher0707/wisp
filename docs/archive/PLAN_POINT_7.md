# Implementation Plan: Point 7 — Real-time Health Monitoring

**Status:** Ready for Implementation  
**Scope:** Monitor system health (memory, CPU, disk), detect service failures (Ollama, MCP), alert on hangs.  
**Dependencies:** None (optional `psutil` for better accuracy).  
**Estimated Effort:** ~900 new lines, ~120 modified lines.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Phase 1: Foundation — `wisp/system_info.py`](#3-phase-1-foundation--wispsystem_infopy)
4. [Phase 2: Core Engine — `wisp/health_monitor.py`](#4-phase-2-core-engine--wisphealth_monitorpy)
5. [Phase 3: Integration Points](#5-phase-3-integration-points)
6. [Phase 4: Alerting & UX](#6-phase-4-alerting--ux)
7. [Phase 5: Testing](#7-phase-5-testing)
8. [File Change Summary](#8-file-change-summary)
9. [Open Questions](#9-open-questions)

---

## 1. Overview

### Goal
Add a lightweight, cross-platform health monitoring subsystem to Wisp that:
- Tracks system resources (memory, CPU, disk) without requiring heavy dependencies.
- Detects when Ollama or MCP servers become unreachable.
- Alerts the user (non-intrusively) when the system is under stress.
- Optionally attempts auto-restart for known services.
- Detects hung operations (e.g., LLM call taking > 30s).

### Non-Goals
- Not a full observability platform (no metrics export, no dashboards).
- Not a process manager (auto-restart is best-effort only).
- Does not replace external monitoring (Datadog, Prometheus, etc.).

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      HealthMonitor                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  MemoryCheck │  │  DiskCheck  │  │   OllamaHealthCheck │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                    │             │
│         └────────────────┴────────────────────┘             │
│                          │                                  │
│                    ┌─────┴─────┐                           │
│                    │  Alert    │                           │
│                    │  Deduplicator                              │
│                    └─────┬─────┘                           │
│                          │                                  │
│         ┌────────────────┼────────────────┐                │
│         ▼                ▼                ▼                │
│    ┌─────────┐     ┌─────────┐     ┌─────────────┐         │
│    │  REPL   │     │  Logger │     │ Auto-Restart│         │
│    │ Banner  │     │  (warn) │     │  (optional) │         │
│    └─────────┘     └─────────┘     └─────────────┘         │
└─────────────────────────────────────────────────────────────┘
```

**Polling Model:** Background `threading.Thread` wakes every N seconds (default 30), runs all registered `HealthCheck`s, and routes results through the deduplicator.

---

## 3. Phase 1: Foundation — `wisp/system_info.py`

**Purpose:** Cross-platform system metrics. Tries `psutil` first, falls back to CLI tools, returns `None` if unavailable.

### API Design

```python
# wisp/system_info.py

from __future__ import annotations
import shutil
import subprocess
import sys
from typing import Optional

class SystemInfo:
    """Cross-platform system metrics without mandatory dependencies."""

    @staticmethod
    def memory_percent() -> Optional[float]:
        """Return percentage of physical memory used (0.0–100.0)."""
        ...

    @staticmethod
    def disk_free_gb(path: str = ".") -> Optional[float]:
        """Return free disk space in gigabytes."""
        ...

    @staticmethod
    def cpu_percent() -> Optional[float]:
        """Return current CPU utilization percentage."""
        ...

    @staticmethod
    def platform() -> str:
        """Return 'darwin', 'linux', 'win32', or 'unknown'."""
        return sys.platform
```

### Implementation Strategy

| Platform | Memory | Disk | CPU |
|----------|--------|------|-----|
| **Preferred** | `psutil.virtual_memory().percent` | `psutil.disk_usage(path).free` | `psutil.cpu_percent(interval=1)` |
| **macOS fallback** | `vm_stat` → parse page counts | `df -h` → parse | `top -l 1` → parse |
| **Linux fallback** | `free -m` → parse | `df -h` → parse | `/proc/loadavg` → normalize by CPU count |
| **Windows fallback** | `wmic OS get TotalVisibleMemorySize` | `wmic logicaldisk` | `wmic cpu get loadpercentage` |
| **Unavailable** | Return `None` | Return `None` | Return `None` |

### Key Decision
Use `shutil.disk_usage()` for disk (stdlib, always available) before falling back to CLI. Memory and CPU are the only ones that truly need fallbacks.

---

## 4. Phase 2: Core Engine — `wisp/health_monitor.py`

### Data Models

```python
# wisp/health_monitor.py

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional
from collections import deque
import threading
import time

@dataclass(frozen=True)
class HealthStatus:
    name: str
    level: str          # "ok" | "warning" | "critical"
    message: str
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class HealthCheck:
    name: str
    check: Callable[[], HealthStatus]
    interval: int = 30               # seconds between checks
    auto_restart: bool = False
    restart_cmd: Optional[str] = None  # shell command to run if critical
```

### Main Class

```python
class HealthMonitor:
    def __init__(self, poll_interval: int = 30):
        self.checks: list[HealthCheck] = []
        self.alerts: deque[HealthStatus] = deque(maxlen=100)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_alert_key: dict[str, datetime] = {}  # dedup cache

    # ── Registration ──
    def register(self, check: HealthCheck) -> None: ...
    def unregister(self, name: str) -> None: ...

    # ── Lifecycle ──
    def start(self) -> None:
        """Spawn background polling thread."""
        ...

    def stop(self) -> None:
        """Signal thread to exit and join."""
        ...

    # ── Queries ──
    def get_alerts(self, level: Optional[str] = None) -> list[HealthStatus]: ...
    def has_critical(self) -> bool: ...
    def has_warnings(self) -> bool: ...
    def summary(self) -> dict[str, HealthStatus]: ...

    # ── Internal ──
    def _poll_loop(self) -> None:
        """Background thread entry point."""
        while not self._stop_event.is_set():
            for check in self.checks:
                status = check.check()
                if status.level != "ok":
                    self._maybe_alert(status)
            self._stop_event.wait(self.poll_interval)

    def _maybe_alert(self, status: HealthStatus) -> None:
        """Deduplicate alerts: same (name, level) within 5 min is suppressed."""
        key = f"{status.name}:{status.level}"
        now = datetime.now()
        last = self._last_alert_key.get(key)
        if last and (now - last) < timedelta(minutes=5):
            return
        self._last_alert_key[key] = now
        with self._lock:
            self.alerts.append(status)
        self._emit(status)

    def _emit(self, status: HealthStatus) -> None:
        """Route alert to REPL banner, logger, and optional auto-restart."""
        ...
```

### Built-in Checks (registered by default in `agent.py`)

```python
# wisp/health_monitor.py

def build_default_checks(agent) -> list[HealthCheck]:
    """Factory for standard checks given an agent instance."""
    checks = []

    # 1. Memory
    checks.append(HealthCheck(
        name="memory",
        check=_memory_check,
        interval=30,
    ))

    # 2. Disk
    checks.append(HealthCheck(
        name="disk",
        check=_disk_check,
        interval=60,
    ))

    # 3. Ollama
    checks.append(HealthCheck(
        name="ollama",
        check=lambda: _ollama_check(agent.client),
        interval=30,
        auto_restart=False,  # user must restart ollama themselves
    ))

    # 4. MCP (if any servers configured)
    if agent.mcp and agent.mcp.servers:
        checks.append(HealthCheck(
            name="mcp",
            check=lambda: _mcp_check(agent.mcp),
            interval=60,
        ))

    return checks
```

### Check Implementations

```python
def _memory_check() -> HealthStatus:
    from wisp.system_info import SystemInfo
    pct = SystemInfo.memory_percent()
    if pct is None:
        return HealthStatus("memory", "ok", "Memory metrics unavailable")
    if pct > 90:
        return HealthStatus("memory", "critical", f"Memory at {pct:.1f}%")
    if pct > 80:
        return HealthStatus("memory", "warning", f"Memory at {pct:.1f}%")
    return HealthStatus("memory", "ok", f"Memory at {pct:.1f}%")

def _disk_check() -> HealthStatus:
    from wisp.system_info import SystemInfo
    free = SystemInfo.disk_free_gb()
    if free is None:
        return HealthStatus("disk", "ok", "Disk metrics unavailable")
    if free < 1:
        return HealthStatus("disk", "critical", f"Disk free: {free:.1f} GB")
    if free < 5:
        return HealthStatus("disk", "warning", f"Disk free: {free:.1f} GB")
    return HealthStatus("disk", "ok", f"Disk free: {free:.1f} GB")

def _ollama_check(client) -> HealthStatus:
    try:
        # Reuse existing health check or lightweight ping
        if client.check_health():
            return HealthStatus("ollama", "ok", "Ollama reachable")
        return HealthStatus("ollama", "critical", "Ollama health check failed")
    except Exception as e:
        return HealthStatus("ollama", "critical", f"Ollama unreachable: {e}")

def _mcp_check(mcp_manager) -> HealthStatus:
    failed = []
    for name, server in mcp_manager.servers.items():
        if not server.is_healthy():
            failed.append(name)
    if failed:
        return HealthStatus("mcp", "warning", f"MCP servers down: {', '.join(failed)}")
    return HealthStatus("mcp", "ok", "All MCP servers healthy")
```

---

## 5. Phase 3: Integration Points

### A. `wisp/ollama_client.py`

Add a lightweight `health_status()` method that returns structured data:

```python
def health_status(self) -> dict:
    """Return {"status": "ok|down", "latency_ms": float, "models_available": int}."""
    ...
```

**Changes:** ~30 lines.

### B. `wisp/mcp.py`

Add `is_healthy()` to the MCP server wrapper / manager:

```python
def is_healthy(self) -> bool:
    """Ping the MCP server (e.g., send an initialize or list-tools request)."""
    ...
```

**Changes:** ~40 lines.

### C. `wisp/agent.py`

**Initialization:**
```python
def __init__(self, config: WispConfig):
    ...
    self.health_monitor = HealthMonitor()
    for check in build_default_checks(self):
        self.health_monitor.register(check)
    self.health_monitor.start()
```

**Before heavy operations (in `run()` / `repl()`):**
```python
if self.health_monitor.has_critical():
    logger.warning("System under stress — operations may be slow")
    # Optional: ask user if they want to continue in interactive mode
```

**Hang detection (in `_execute_loop()`):**
```python
operation_start = time.time()
# ... LLM call ...
elapsed = time.time() - operation_start
if elapsed > 30:
    self.health_monitor._maybe_alert(
        HealthStatus("hang", "warning", f"Operation took {elapsed:.1f}s")
    )
```

**Cleanup:**
```python
finally:
    self.health_monitor.stop()
    self.mcp.shutdown()
    _restore_signal_handler()
```

**Changes:** ~60 lines.

### D. `wisp/__main__.py`

Add `health` subcommand:

```bash
wisp health              # One-shot snapshot
wisp health --watch      # Continuous monitoring (Ctrl+C to stop)
```

**Implementation:**
```python
def cmd_health(watch=False):
    monitor = HealthMonitor()
    # Register default checks without needing a full agent
    ...
    if watch:
        print("Watching health (Ctrl+C to stop)...")
        try:
            while True:
                _print_summary(monitor.summary())
                time.sleep(5)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        _print_summary(monitor.summary())
```

**Changes:** ~50 lines.

---

## 6. Phase 4: Alerting & UX

### REPL Banner Display

Alerts are shown **before the next prompt**, not mid-stream:

```python
def _print_health_banner(monitor: HealthMonitor):
    alerts = monitor.get_alerts()
    critical = [a for a in alerts if a.level == "critical"]
    warnings = [a for a in alerts if a.level == "warning"]

    for a in critical[-2:]:  # Show last 2 critical
        print(f"\033[91m🚨 [{a.name}] {a.message}\033[0m")
    for a in warnings[-2:]:  # Show last 2 warnings
        print(f"\033[93m⚠️  [{a.name}] {a.message}\033[0m")
```

Called at the top of the REPL loop before printing `➜ `.

### Auto-Restart Logic

```python
if check.auto_restart and status.level == "critical" and check.restart_cmd:
    print(f"🔄 Attempting auto-restart: {check.name}")
    try:
        subprocess.run(check.restart_cmd, shell=True, timeout=30)
        time.sleep(5)
        # Re-run check to verify
        new_status = check.check()
        if new_status.level == "ok":
            print(f"✅ {check.name} recovered")
        else:
            print(f"❌ {check.name} still down")
    except Exception as e:
        print(f"❌ Auto-restart failed: {e}")
```

**Default:** `auto_restart=False` for all checks. User can opt-in via config.

### Alert Deduplication

| Key | Window | Behavior |
|-----|--------|----------|
| `memory:warning` | 5 min | Suppress repeat warnings |
| `memory:critical` | 5 min | Suppress repeat criticals |
| `ollama:critical` | 2 min | Faster retry for services |

If a check flaps (warning → ok → warning), the ok resets the dedup window.

---

## 7. Phase 5: Testing

### `tests/test_system_info.py` (~100 lines)

- Mock `psutil` presence/absence.
- Mock platform-specific CLI outputs (`vm_stat`, `free`, `wmic`).
- Verify graceful `None` return when all methods fail.

### `tests/test_health_monitor.py` (~120 lines)

- Mock `HealthCheck` that returns predetermined statuses.
- Verify threading lifecycle (`start()`, `stop()`).
- Verify deduplication (same alert within 5 min is suppressed).
- Verify `has_critical()`, `has_warnings()`, `summary()`.
- Verify auto-restart command is invoked when configured.

### `tests/test_agent_health.py` (~50 lines, optional)

- Verify monitor is started in `__init__` and stopped in cleanup.
- Verify hang detection triggers after slow operation.

---

## 8. File Change Summary

| File | Action | Lines | Notes |
|------|--------|-------|-------|
| `wisp/system_info.py` | **New** | ~200 | Cross-platform metrics |
| `wisp/health_monitor.py` | **New** | ~300 | Core engine + built-in checks |
| `wisp/ollama_client.py` | Modify | ~30 | Add `health_status()` |
| `wisp/mcp.py` | Modify | ~40 | Add `is_healthy()` |
| `wisp/agent.py` | Modify | ~60 | Init, hang detection, cleanup |
| `wisp/__main__.py` | Modify | ~50 | `health` subcommand |
| `tests/test_system_info.py` | **New** | ~100 | Platform mocking |
| `tests/test_health_monitor.py` | **New** | ~120 | Threading + dedup |
| **Total** | | **~900 new, ~180 modified** | |

---

## 9. Open Questions

1. **Should we add `psutil` as an optional dependency?**
   - Pro: Much more accurate and faster than CLI parsing.
   - Con: Adds a C-extension dependency that can fail to build.
   - **Recommendation:** Add to `pyproject.toml` as `wisp[monitoring]` optional extra. Use it if present, fall back otherwise.

2. **Should auto-restart be enabled by default for Ollama?**
   - Pro: Seamless recovery.
   - Con: Could mask real issues; Ollama restart can take 10–30s.
   - **Recommendation:** Default to `False`. User opts in via `wisp config --set health.auto_restart_ollama=true`.

3. **Should hang detection threshold be configurable?**
   - **Recommendation:** Yes. Add `config.hang_threshold_seconds` (default 30).

4. **Should health alerts be sent to the LLM context?**
   - Pro: Agent can adapt behavior (e.g., "system is low on memory, avoid spawning subagents").
   - Con: Increases token usage.
   - **Recommendation:** Phase 2 feature. Inject a short `## System Health` block into the system prompt only when `has_critical()` is true.

---

*End of Plan — Ready for implementation approval.*
