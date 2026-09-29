"""`python -m wisp_net mcp --warmup N` starts the lab N simulated seconds in.

An evaluation needs the fault already developed, and the same way every time. Real-time start makes
the state depend on how long the client took to connect. `--warmup` advances the simulated clock
before the server answers its first request; with a tiny `--speed` the world is then effectively
frozen while an agent works.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from wisp.mcp.manager import MCPServerConfig, call_tool, connect_server, disconnect_server

REPO = str(Path(__file__).resolve().parents[2])


def _status_and_alerts(*extra: str):
    config = MCPServerConfig(name="net", command=sys.executable,
                             args=["-m", "wisp_net", "mcp", "--scenario", "optic-degradation",
                                   "--speed", "0.001", *extra],
                             env={"PYTHONPATH": REPO})
    srv = connect_server(config)
    try:
        status = json.loads(call_tool(srv, "net_status", {}))
        alerts = json.loads(call_tool(srv, "net_alerts", {}))
        return status, alerts
    finally:
        disconnect_server(srv)


def test_a_warmed_lab_already_shows_the_developed_fault():
    status, alerts = _status_and_alerts("--warmup", "900")
    assert status["elapsed_s"] >= 900
    assert any("leaf2" in json.dumps(a) for a in alerts)


def test_without_warmup_the_fault_has_not_happened_yet():
    status, alerts = _status_and_alerts()
    assert status["elapsed_s"] < 100
    assert alerts == []
