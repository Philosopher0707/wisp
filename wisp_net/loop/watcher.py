"""Closed loop: alerts become incidents, incidents become headless agent turns.

The watcher reads the alerts topic like any other consumer. Major and critical alert
openings are gathered for `debounce_s` (lab time) into one incident, so a link failure
and the BGP alerts it causes are one incident, not five. An incident whose alerts were
all seen within `cooldown_s` is not dispatched again.

Autonomy tiers (TM Forum style, lowest first):
  observe   record the incident; no agent runs
  diagnose  the agent investigates and reports; wisp runs it read-only
  propose   the agent also compiles and verifies a change (what-if); still read-only
  act       the agent may apply — the platform's verification, policy, change windows and
            operator approvals still gate every change, exactly as for a human-driven agent

The agent is any command (default: a headless wisp turn with the orchestrator skill). It
receives the platform's URL and agent-token file in its environment, and runs on its own
thread so the network keeps moving while it thinks.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from wisp_net.telemetry.alerts import SEVERITY_RANK
from wisp_net.telemetry.bus import TOPIC_ALERTS, Consumer

TIERS = ("observe", "diagnose", "propose", "act")
PERMISSION_FOR_TIER = {"diagnose": "read_only", "propose": "read_only", "act": "auto_edit"}
DEFAULT_COMMAND = ["wisp", "run", "{prompt}", "--skill", "net-orchestrator"]

_TIER_ASK = {
    "diagnose": "Diagnose it: establish the cause with evidence and report. Do not propose or apply changes.",
    "propose": ("Diagnose it, then propose the remedy as an intent, compile it and verify it with net_what_if. "
                "Report the verification and the policy verdict. Do not apply."),
    "act": ("Diagnose it and, if a verified change is allowed by the change policy, apply it with a rationale that "
            "cites your evidence. If the policy requires approval, open the request and report its id. "
            "Escalate everything the orchestrator skill says to escalate."),
}


@dataclass
class Incident:
    incident_id: str
    opened: float
    alerts: list[dict[str, Any]] = field(default_factory=list)
    status: str = "gathering"  # gathering | recorded | dispatched | finished | suppressed
    run: dict[str, Any] = field(default_factory=dict)

    def keys(self) -> set[str]:
        return {f"{a['rule']}|{a['device']}|{a['subject']}" for a in self.alerts}

    def summary(self) -> str:
        return "; ".join(f"[{a['severity']}] {a['message']} ({a['alert_id']})" for a in self.alerts)


Runner = Callable[[Incident, str, dict[str, str]], dict[str, Any]]


def subprocess_runner(command: list[str], timeout_s: float = 1800.0) -> Runner:
    def run(incident: Incident, prompt: str, env: dict[str, str]) -> dict[str, Any]:
        argv = [part.replace("{prompt}", prompt) for part in command]
        try:
            done = subprocess.run(argv, env={**os.environ, **env}, capture_output=True, text=True, timeout=timeout_s)
            return {"exit_code": done.returncode, "output_tail": (done.stdout or "")[-4000:],
                    "stderr_tail": (done.stderr or "")[-1000:]}
        except FileNotFoundError as exc:
            return {"exit_code": 127, "output_tail": "", "stderr_tail": f"agent command not found: {exc}"}
        except subprocess.TimeoutExpired:
            return {"exit_code": 124, "output_tail": "", "stderr_tail": f"agent run exceeded {timeout_s:.0f}s"}
    return run


class Watcher:
    def __init__(self, service: Any, runner: Runner | None, tier: str = "diagnose", debounce_s: float = 20.0,
                 cooldown_s: float = 600.0, min_severity: str = "major", env: dict[str, str] | None = None) -> None:
        if tier not in TIERS:
            raise ValueError(f"autonomy tier must be one of {', '.join(TIERS)}")
        self.s = service
        self.runner = runner
        self.tier = tier
        self.debounce_s, self.cooldown_s = debounce_s, cooldown_s
        self.floor = SEVERITY_RANK[min_severity]
        self.env = dict(env or {})
        self.consumer = Consumer(service.bus, TOPIC_ALERTS, from_end=True)
        self.incidents: list[Incident] = []
        self._open: Incident | None = None
        self._last_seen: dict[str, float] = {}
        self._seq = 0
        self._threads: list[threading.Thread] = []
        # Alerts already open when the watcher starts are live incidents too; the consumer
        # starts at the end of the topic, so they are adopted here rather than missed.
        for alert in service.alerts.list("open"):
            if SEVERITY_RANK[alert.severity] >= self.floor:
                self._gather(alert.to_dict(), service.net.now)

    def poll(self) -> list[Incident]:
        """Consume new alert changes; return incidents that closed their gathering window."""
        now = self.s.net.now
        for rec in self.consumer.poll():
            change = rec.value
            if change.get("change") not in ("opened", "reopened", "severity"):
                continue
            if SEVERITY_RANK.get(change.get("severity", "info"), 0) < self.floor:
                continue
            self._gather(change, now)
        ready: list[Incident] = []
        if self._open is not None and now - self._open.opened >= self.debounce_s:
            incident, self._open = self._open, None
            fresh = [k for k in incident.keys() if now - self._last_seen.get(k, -1e18) > self.cooldown_s]
            for k in incident.keys():
                self._last_seen[k] = now
            incident.status = "suppressed" if not fresh else "recorded"
            self.incidents.append(incident)
            self.s.ledger.append("incident_opened", {"incident_id": incident.incident_id, "tier": self.tier,
                                                     "status": incident.status, "alerts": incident.alerts}, now)
            if incident.status == "recorded":
                ready.append(incident)
                self._dispatch(incident)
        return ready

    def _gather(self, alert: dict[str, Any], now: float) -> None:
        if self._open is None:
            self._seq += 1
            self._open = Incident(f"I{self._seq:04d}", now)
        if alert["alert_id"] not in {a["alert_id"] for a in self._open.alerts}:
            self._open.alerts.append({k: alert.get(k) for k in
                                      ("alert_id", "rule", "device", "subject", "severity", "message")})

    def prompt_for(self, incident: Incident) -> str:
        return (f"Network incident {incident.incident_id}: {incident.summary()}. "
                f"Use the net-orchestrator skill and the mcp__net__* tools. {_TIER_ASK[self.tier]}")

    def _dispatch(self, incident: Incident) -> None:
        if self.tier == "observe" or self.runner is None:
            return
        prompt = self.prompt_for(incident)
        env = {**self.env, "WISP_PERMISSION_MODE": PERMISSION_FOR_TIER[self.tier],
               "WISP_NET_INCIDENT": incident.incident_id}
        incident.status = "dispatched"
        self.s.ledger.append("agent_dispatched", {"incident_id": incident.incident_id, "tier": self.tier,
                                                  "permission_mode": env["WISP_PERMISSION_MODE"],
                                                  "prompt": prompt}, self.s.net.now)
        runner = self.runner

        def work() -> None:
            result = runner(incident, prompt, env)
            incident.run, incident.status = result, "finished"
            self.s.ledger.append("agent_finished", {"incident_id": incident.incident_id, **result}, self.s.net.now)

        thread = threading.Thread(target=work, name=f"wisp-net-agent-{incident.incident_id}", daemon=True)
        self._threads.append(thread)
        thread.start()

    def join(self, timeout: float = 30.0) -> None:
        for t in self._threads:
            t.join(timeout)
