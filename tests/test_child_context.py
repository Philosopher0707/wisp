"""A child's first messages come from one builder, within stated bounds (design S3).

`docs/harness/subagent-orchestration-design.md` §4.2. Observed on `main` before this slice:

* context files were read with no containment: an absolute path outside the workspace (a key file,
  a shell rc) went into the child's system prompt, and so to the provider;
* the prompt's "Allowed Tools" listed what the contract asked for, not what the child had after the
  permission mode and the depth limit;
* a DAG node's upstream results were pasted into its task as instructions ("use them, do not redo
  this work"), not as another agent's unverified claim;
* nothing bounded the task, and nothing recorded what a child was given, so a bad run could not be
  replayed with the same input.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_subagent_controls_reach_the_child import _contract, _wire


@pytest.fixture
def wired(tmp_path, monkeypatch):
    root, stub, ws = _wire(tmp_path, monkeypatch)
    yield root, stub, ws
    stub.server.shutdown()
    stub.server.server_close()


def _seen(stub, arg):
    [req] = [r for r in stub.of("say", arg)]
    return req


class TestFiles:
    def test_a_file_outside_the_workspace_is_not_given_to_the_child(self, wired, tmp_path):
        root, stub, ws = wired
        outside = tmp_path / "outside.txt"
        outside.write_text("OUTSIDE-CONTENT\n")
        (ws / "inside.txt").write_text("INSIDE-CONTENT\n")
        asyncio.run(root.subagent_orchestrator.run(_contract(
            "f", "SCN{say:files}", context_files=[str(outside), "../outside.txt", "inside.txt"])))
        req = _seen(stub, "files")
        assert "INSIDE-CONTENT" in req["task"]  # files are data: the task message, not the system prompt
        assert "OUTSIDE-CONTENT" not in req["task"] + req["system"]
        assert req["task"].count("outside the workspace") == 2

    def test_secrets_in_a_context_file_are_redacted(self, wired):
        root, stub, ws = wired
        key = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
        (ws / "settings.py").write_text(f'OPENAI_API_KEY = "{key}"\nDEBUG = True\n')
        asyncio.run(root.subagent_orchestrator.run(_contract("s", "SCN{say:secret}", context_files=["settings.py"])))
        req = _seen(stub, "secret")
        assert key not in req["task"] + req["system"]
        assert "REDACTED" in req["task"] and "DEBUG = True" in req["task"]

    def test_files_are_bounded_and_the_cut_is_stated(self, wired):
        root, stub, ws = wired
        for i in range(6):
            (ws / f"big{i}.txt").write_text(f"FILE{i}-" + "x" * 20_000)
        asyncio.run(root.subagent_orchestrator.run(_contract(
            "b", "SCN{say:big}", context_files=[f"big{i}.txt" for i in range(6)])))
        req = _seen(stub, "big")
        from wisp.multi_agent.child_context import FILES_TOTAL_CHARS, TASK_CHARS
        assert len(req["task"]) < FILES_TOTAL_CHARS + TASK_CHARS
        assert "FILE0-" in req["task"] and "omitted" in req["task"]

    def test_large_files_do_not_cost_the_child_its_role_prompt(self, wired):
        """The assembler drops an over-budget system section whole: with files in the system prompt,
        a child given large files lost its role's rules with them."""
        root, stub, ws = wired
        for i in range(3):
            (ws / f"large{i}.txt").write_text("z" * 12_000)
        asyncio.run(root.subagent_orchestrator.run(_contract(
            "r", "SCN{say:role}", role="researcher", context_files=[f"large{i}.txt" for i in range(3)])))
        assert "Researcher agent" in _seen(stub, "role")["system"]


class TestTaskAndTools:
    def test_a_long_task_is_cut_with_a_stated_cut(self, wired):
        root, stub, _ = wired
        from wisp.multi_agent.child_context import TASK_CHARS
        asyncio.run(root.subagent_orchestrator.run(_contract("t", "SCN{say:long} " + "y" * 30_000)))
        task = _seen(stub, "long")["task"]
        assert len(task) <= TASK_CHARS + 200 and "characters of the task were cut" in task

    def test_the_prompt_lists_the_tools_the_child_actually_has(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_TOOL_PROFILE", "full")
        root, stub, _ = _wire(tmp_path, monkeypatch, permission_mode="full")
        try:
            asyncio.run(root.subagent_orchestrator.run(_contract(
                "last", "SCN{say:tools}", _subagent_depth=1, tools=["read_file", "spawn", "fanout"])))
        finally:
            stub.server.shutdown()
            stub.server.server_close()
        req = _seen(stub, "tools")
        assert req["tools"] == ["read_file"]
        listed = req["system"].split("## Allowed Tools", 1)[1].split("\n\n", 1)[0]
        assert "read_file" in listed and "spawn" not in listed and "fanout" not in listed


class TestUpstreamResults:
    def test_a_dag_nodes_upstream_result_arrives_labelled_as_a_claim_and_bounded(self, wired):
        root, stub, ws = wired
        from wisp.multi_agent.child_context import UPSTREAM_CHARS
        asyncio.run(root.tool_executor._orchestrate_dag({"nodes": [
            {"name": "a", "task": "SCN{say:up}"},
            {"name": "b", "task": "SCN{say:down}", "depends_on": ["a"]},
        ]}, str(ws)))
        task = _seen(stub, "down")["task"]
        assert "### a (another agent's report, unverified)\nANSWER say up" in task
        assert "do not redo" not in task.lower()
        assert len(task) < UPSTREAM_CHARS + 2_000


    def test_a_large_upstream_report_is_cut_and_the_cut_stated(self):
        from wisp.multi_agent import SubagentContract
        from wisp.multi_agent.child_context import UPSTREAM_CHARS, UPSTREAM_TOTAL_CHARS, build_child_context

        contract = SubagentContract(name="n", task="t", metadata={"_upstream": {
            "big": "u" * 50_000, "second": "v" * 50_000, "third": "w" * 50_000, "fourth": "x"}})
        given = build_child_context(contract, workspace=".", effective_tools=())
        assert given.summary["upstream"]["big"] <= UPSTREAM_CHARS + 100
        assert "characters of big's report were cut" in given.task
        assert "more reports omitted" in given.task
        assert len(given.task) < UPSTREAM_TOTAL_CHARS + 2_000


class TestTheRecordSaysWhatTheChildWasGiven:
    def test_the_record_carries_sizes_and_a_digest(self, wired):
        root, _, ws = wired
        (ws / "a.txt").write_text("alpha\n")
        orch = root.subagent_orchestrator
        asyncio.run(orch.run(_contract("d1", "SCN{say:dg}", context_files=["a.txt"])))
        asyncio.run(orch.run(_contract("d2", "SCN{say:dg}", context_files=["a.txt"])))
        first, second = root.agent_registry.list()
        given = json.loads(first.links["context"])
        assert given["files"] == ["a.txt"] and given["task_chars"] > 0 and len(given["digest"]) == 64
        assert json.loads(second.links["context"])["digest"] == given["digest"], "same input, same digest"


class TestOneBuilder:
    def test_only_the_builder_renders_context_files_and_upstream_results(self):
        import pathlib

        for marker in ("## Context Files", "another agent's report"):
            renderers = sorted(str(p) for p in pathlib.Path("wisp").rglob("*.py")
                               if marker in p.read_text(encoding="utf-8"))
            assert renderers == ["wisp/multi_agent/child_context.py"], (marker, renderers)
