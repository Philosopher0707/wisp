"""W2: /spawn TTY-only dispatch gating + abort skips the orchestrator.

Gate under test: wisp.cli.ui.guard.run_spawn_gate (real module path).
Command under test: wisp.repl.commands.agents.cmd_spawn.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from wisp.repl.commands.agents import cmd_spawn


class _StubOrch:
    """Minimal orchestrator double recording run() contracts."""

    def __init__(self):
        self.calls = []

    async def run(self, contract):
        self.calls.append(contract)
        return SimpleNamespace(
            success=True,
            timed_out=False,
            elapsed_seconds=0.1,
            iterations_used=1,
            output="ok",
        )


def _agent_with_orch(orch):
    agent = MagicMock(spec=["runtime", "messages", "session", "config"])
    agent.runtime.orchestrator = orch
    return agent


def test_spawn_non_tty_proceeds_without_prompt(monkeypatch):
    """Pipes/CI (isatty False) never block on a prompt — dispatch proceeds."""
    import wisp.cli.ui.guard as G

    orch = _StubOrch()
    agent = _agent_with_orch(orch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    gate_calls = []
    monkeypatch.setattr(
        G, "run_spawn_gate", lambda *a, **k: gate_calls.append((a, k)) or "approve"
    )
    cmd_spawn(agent, "do the thing")
    assert len(orch.calls) == 1
    assert gate_calls == []


def test_spawn_abort_skips_dispatch(monkeypatch, capsys):
    """A rejected gate never reaches the orchestrator (wisp.cli.ui.guard.run_spawn_gate)."""
    import wisp.cli.ui.guard as G

    orch = _StubOrch()
    agent = _agent_with_orch(orch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(G, "run_spawn_gate", lambda *a, **k: "reject")
    cmd_spawn(agent, "do the thing")
    assert orch.calls == []
    assert "cancelled" in capsys.readouterr().out.lower()
