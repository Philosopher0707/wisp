"""The whole agent pipeline, driven by a scripted model: the positive control for the evaluation.

A real 3B model scored 0/6 with no structured tool call in any run. That is only a statement about the
model if a model that *does* call tools can pass. This runs the real `wisp` CLI, the real MCP server
and the real lab against `fake_ollama.FakeOllama`, which asks for one structured `net_alerts` call and
then answers from the tool result alone. If the harness, the tool advertisement, the MCP round trip or
the scoring were broken, this would fail and the 0/6 would mean nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import FakeOllama  # noqa: E402

from wisp_net import evaluation as ev  # noqa: E402

REPO = str(Path(__file__).resolve().parents[2])
CASE = next(c for c in ev.CASES if c.scenario == "optic-degradation")


@pytest.fixture()
def wired(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", REPO)
    with FakeOllama() as fake:
        monkeypatch.setenv("WISP_OLLAMA_URL", fake.url)
        yield fake


def _run(case=CASE):
    return ev.run_case(case, model="fake:1b", provider="ollama", wisp_cmd=[sys.executable, "-m", "wisp"],
                       passthrough=["WISP_OLLAMA_URL", "PYTHONPATH"], timeout_s=180)


def test_a_model_that_calls_tools_passes_through_the_real_pipeline(wired):
    s = _run()
    assert s.passed, s.reasons
    assert s.net_calls == 1 and s.actuating_calls == [] and s.unknown_devices == []
    assert "leaf2" in s.answer and "Ethernet50" in s.answer and "rx_power_low" in s.answer


def test_the_mcp_tools_really_reach_the_model_and_the_result_comes_back(wired):
    _run()
    first, second = wired.requests[0], wired.requests[1]
    offered = {t["function"]["name"] for t in first["tools"]}
    assert "mcp__net__net_alerts" in offered and "mcp__net__net_optics_forecast" in offered
    returned = [m for m in second["messages"] if m.get("role") == "tool"]
    assert returned and "leaf2" in returned[-1]["content"], "the alert data must reach the model"


def test_no_fault_no_pass(wired):
    """Same pipeline, no warmup: the fault has not happened, so evidence cannot support the diagnosis."""
    early = ev.Case(CASE.scenario, 0, CASE.prompt, CASE.facts)
    s = _run(early)
    assert not s.passed and s.missing_facts, "with nothing wrong yet, a model answering from evidence cannot name the fault"


def test_read_only_is_really_enforced_on_the_print_path(monkeypatch):
    """A model that reaches for `net_apply_change` is refused by wisp, and the run is scored as a violation.

    `wisp --print` used to run with `permission_mode="full"` whatever `WISP_PERMISSION_MODE` said, so the
    evaluation's "read_only" was a claim, not a fact. Here the scripted model asks for an actuating tool;
    the result it gets back must be a refusal from the gate, not the tool's own answer.
    """
    monkeypatch.setenv("PYTHONPATH", REPO)
    with FakeOllama(first_call="mcp__net__net_apply_change") as fake:
        monkeypatch.setenv("WISP_OLLAMA_URL", fake.url)
        s = _run()
    assert s.actuating_calls == ["net_apply_change"] and not s.passed
    returned = [m for m in fake.requests[1]["messages"] if m.get("role") == "tool"]
    denial = json.loads(returned[-1]["content"])
    assert denial["status"] == "POLICY_DENIED" and denial["executed"] is False
    assert "READ_ONLY" in denial["reason"]
