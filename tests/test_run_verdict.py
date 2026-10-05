"""One verdict, one way of describing a failure, shared by every surface that runs agents.

`/swarm` (REPL) had its own copy of the logic (PR #76); `fanout` (the executor tool) summarised a run separately and showed the
model each agent's raw provider JSON. The verdict (complete / partial / failed) and the plain-words description of a failure
("out of credit: add credit or raise the key's limit") are domain logic, not presentation: they live in `multi_agent.verdict`
and `core.recovery`, and the REPL command and `fanout` both use them.

`fanout`'s envelope `status` is deliberately untouched: it means "the tool call executed", and the agents' outcome is in
`data.ok` (`tests/test_spawn_fanout.py` pins that: "tool call succeeded, subagent failed"). Only fields are added.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_spawn_fanout import _mk_te
from wisp.core.recovery import describe_failure
from wisp.multi_agent.task import SubagentResult
from wisp.multi_agent.verdict import distinct_failures, verdict

BILLING = ("Incomplete provider round (API error 402: This request requires more credits — the provider refused this "
           "request on billing: the account is out of credit)")


def _r(ok: bool, error: str | None = None, task: str = "t") -> SubagentResult:
    return SubagentResult(task_id=task, success=ok, output="out" if ok else "", error=error)


# ── describe_failure (core/recovery) ─────────────────────────────────────────────────────────────────────────


def test_a_billing_refusal_is_described_with_its_remedy():
    text = describe_failure(BILLING)
    assert "out of credit" in text and "Add credit or raise the key's limit" in text


def test_a_rejected_key_is_described_with_its_remedy():
    assert "HTTP 401" in describe_failure("Incomplete provider round (API error 401: bad key)")


def test_other_errors_are_kept_in_their_own_words_and_bounded():
    assert describe_failure("tool crashed: KeyError 'x'") == "tool crashed: KeyError 'x'"
    assert describe_failure(None) == "no error reported"
    assert len(describe_failure("x" * 1000)) == 300
    assert describe_failure("a\n  b   c") == "a b c", "whitespace is collapsed to one line"


# ── verdict (multi_agent) ────────────────────────────────────────────────────────────────────────────────────


def test_verdict_is_complete_partial_or_failed():
    assert verdict([_r(True), _r(True)]) == "complete"
    assert verdict([_r(True), _r(False, "e")]) == "partial"
    assert verdict([_r(False, "e"), _r(False, "e")]) == "failed"
    assert verdict([]) == "failed"


def test_identical_failures_are_one_finding_with_every_label_that_hit_it():
    results = [_r(False, BILLING), _r(True), _r(False, BILLING), _r(False, "tool crashed")]
    groups = distinct_failures(results, labels=["coder", "reviewer", "tester", "researcher"])
    assert [(why[:20], who) for why, who in groups] == [
        (describe_failure(BILLING)[:20], ["coder", "tester"]),
        ("tool crashed", ["researcher"]),
    ]


def test_labels_default_to_the_task_id():
    groups = distinct_failures([_r(False, "boom", task="fanout-0"), _r(False, "boom", task="fanout-1")])
    assert groups == [("boom", ["fanout-0", "fanout-1"])]


def test_no_failures_means_no_groups():
    assert distinct_failures([_r(True), _r(True)]) == []


# ── fanout: fields added, contract kept ──────────────────────────────────────────────────────────────────────


async def _fanout(tmp_path, results):
    orch = MagicMock()
    orch.run_parallel = AsyncMock(return_value=results)
    te = _mk_te(tmp_path, orch)
    out = await te._fanout({"tasks": [{"task": "A"}, {"task": "B"}], "mode": "blocking"}, str(tmp_path))
    return json.loads(out)


@pytest.mark.asyncio
async def test_fanout_when_every_agent_failed_says_so_in_words_and_keeps_status_ok(tmp_path):
    data = await _fanout(tmp_path, [_r(False, BILLING, "fanout-0-coder"), _r(False, BILLING, "fanout-1-tester")])
    assert data["status"] == "ok", "the envelope means 'the tool call executed'; the agents' outcome is data.ok"
    inner = data["data"]
    assert inner["ok"] is False and inner["verdict"] == "failed"
    assert len(inner["failures"]) == 1, "two identical refusals are one finding"
    assert inner["failures"][0]["tasks"] == ["fanout-0-coder", "fanout-1-tester"]
    assert "out of credit" in inner["failures"][0]["reason"]
    assert "0/2 subagents succeeded" in inner["summary"], "the existing summary is unchanged"
    assert inner["results"][0]["error"] == BILLING, "per-agent raw errors are still there"


@pytest.mark.asyncio
async def test_fanout_partial_failure_adds_the_verdict_and_the_distinct_reason(tmp_path):
    data = await _fanout(tmp_path, [_r(True, None, "fanout-0-coder"), _r(False, "test failed", "fanout-1-tester")])
    inner = data["data"]
    assert inner["ok"] is False and inner["verdict"] == "partial"
    assert inner["failures"] == [{"reason": "test failed", "tasks": ["fanout-1-tester"]}]
    assert "1/2 subagents succeeded" in inner["summary"]


@pytest.mark.asyncio
async def test_fanout_when_everything_worked_has_no_failures(tmp_path):
    data = await _fanout(tmp_path, [_r(True, None, "a"), _r(True, None, "b")])
    inner = data["data"]
    assert inner["ok"] is True and inner["verdict"] == "complete" and inner["failures"] == []


def test_the_repl_swarm_command_uses_the_shared_code_not_a_copy():
    import ast
    from pathlib import Path

    src = Path("wisp/repl/commands/agents.py").read_text(encoding="utf-8")
    names = {n.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
    assert not names & {"_swarm_verdict", "_describe_failure"}, "the REPL command must not carry its own copy"
    assert "from wisp.multi_agent.verdict import" in src
