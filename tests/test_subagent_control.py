"""Every subagent limit is checked twice and every refusal says what to do (design S2).

`docs/harness/subagent-orchestration-design.md` §4.3 and §8.1. Observed on `main` before this slice:

* a child at the depth limit was still offered `spawn`, so it spent a round to be refused;
* a child spawning while the pool was full waited for a slot its own ancestors held: with the
  pool full of ancestors, the chain deadlocked until the contract's timeout (PR #146);
* there was no limit on spawns per session, and the owner's largest real session re-ran one task
  five times after it had already succeeded (`.wisp/subagent_results.jsonl`, 2026-10-14);
* a model could ask `spawn` for tools its role does not allow;
* a child whose output failed its declared schema was reported `success=True`.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.test_subagent_controls_reach_the_child import _contract, _wire

SPAWNING = {"spawn", "fanout", "spawn_background", "subagent_send",
            "orchestrate_vote", "orchestrate_map_reduce", "orchestrate_chain", "orchestrate_dag"}


@pytest.fixture
def full(tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_TOOL_PROFILE", "full")
    made = []

    def make(**overrides):
        base = tmp_path / f"r{len(made)}"
        base.mkdir()
        root, stub, ws = _wire(base, monkeypatch, permission_mode="full", **overrides)
        made.append(stub)
        return root, stub, ws
    yield make
    for stub in made:
        stub.server.shutdown()
        stub.server.server_close()


class TestDepthIsCheckedTwice:
    def test_a_child_at_the_limit_is_not_offered_any_spawning_tool(self, full):
        root, stub, _ = full()
        asyncio.run(root.subagent_orchestrator.run(_contract("last", "SCN{say:last}", _subagent_depth=1)))
        offered = set(stub.of("say", "last")[-1]["tools"])
        assert not SPAWNING & offered, sorted(SPAWNING & offered)
        assert "read_file" in offered

    def test_a_child_below_the_limit_still_may_spawn(self, full):
        root, stub, _ = full()
        asyncio.run(root.subagent_orchestrator.run(_contract("first", "SCN{say:first}")))
        assert "spawn" in stub.of("say", "first")[-1]["tools"]

    def test_the_depth_refusal_tells_the_model_what_to_do(self, full):
        root, _, _ = full()
        r = asyncio.run(root.subagent_orchestrator.run(_contract("deep", "SCN{say:deep}", _subagent_depth=2)))
        assert r.refusal_reason == "depth"
        assert "yourself" in r.output and "Do not retry" in r.output


class TestNoChildWaitsOnItsAncestors:
    def test_a_child_is_refused_rather_than_queued_when_the_pool_is_full(self, full):
        """Pool of 2, depth limit 5: r0 and its child hold both slots; the grandchild used to wait
        for a slot only its ancestors could free."""
        root, _, _ = full(subagent_pool_size=2, max_subagent_depth=5)
        started = time.monotonic()
        r = asyncio.run(root.subagent_orchestrator.run(_contract("r0", "SCN{recurse}", timeout_seconds=40)))
        assert time.monotonic() - started < 20, "the chain waited on its own ancestors"
        assert r.success, r.output
        refused = [x for x in root.agent_registry.list() if x.status == "refused"]
        assert [x.refusal_reason for x in refused] == ["concurrency"], refused
        assert "Do not retry" in refused[0].error


class TestSpawnsPerSession:
    def test_the_limit_refuses_and_the_count_is_shown_before_it(self, full, monkeypatch):
        monkeypatch.setenv("WISP_MAX_SPAWNS_PER_SESSION", "4")
        root, stub, _ = full()
        from wisp.tools.context import root_session_id

        async def five():
            root_session_id.set("s-limit")
            return [await root.subagent_orchestrator.run(_contract(f"c{i}", f"SCN{{say:c{i}}}")) for i in range(5)]
        results = asyncio.run(five())
        assert [r.success for r in results] == [True, True, True, True, False]
        assert "4 of 4" in results[3].output and "of 4" not in results[0].output
        assert results[4].refusal_reason == "spawn_limit" and not stub.of("say", "c4")

    def test_sessions_are_counted_separately(self, full, monkeypatch):
        monkeypatch.setenv("WISP_MAX_SPAWNS_PER_SESSION", "1")
        root, _, _ = full()
        from wisp.tools.context import root_session_id

        async def one(session, name):
            root_session_id.set(session)
            return await root.subagent_orchestrator.run(_contract(name, f"SCN{{say:{name}}}"))
        assert asyncio.run(one("a", "x")).success and asyncio.run(one("b", "y")).success


def _reusable(name, task, **kw):
    """`_contract` turns the result cache off; the guard only answers callers that allow reuse."""
    c = _contract(name, task, **kw)
    c.cache = True
    return c


class TestDuplicateGuard:
    def test_a_task_that_already_succeeded_is_not_run_again(self, full):
        root, stub, _ = full()
        orch = root.subagent_orchestrator
        first = asyncio.run(orch.run(_reusable("a", "SCN{say:dup}  ")))
        again = asyncio.run(orch.run(_reusable("b", "  scn{say:dup}")))
        assert len(stub.of("say", "dup")) == 1
        assert again.success and again.agent_id == first.agent_id
        assert "ALREADY DONE" in again.output and "ANSWER say dup" in again.output

    def test_another_role_or_a_failed_first_run_does_run_again(self, full):
        root, stub, _ = full()
        orch = root.subagent_orchestrator
        asyncio.run(orch.run(_reusable("a", "SCN{say:roles}", role="researcher")))
        asyncio.run(orch.run(_reusable("b", "SCN{say:roles}", role="reviewer")))
        assert len(stub.of("say", "roles")) == 2
        asyncio.run(orch.run(_reusable("c", "SCN{silent:5}", timeout_seconds=2)))
        asyncio.run(orch.run(_reusable("d", "SCN{silent:5}", timeout_seconds=2)))
        assert len(stub.of("silent")) == 2

    def test_another_session_or_a_caller_that_opts_out_runs_again(self, full):
        root, stub, _ = full()
        from wisp.tools.context import root_session_id

        async def in_session(session, contract):
            root_session_id.set(session)
            return await root.subagent_orchestrator.run(contract)
        asyncio.run(in_session("s1", _reusable("a", "SCN{say:sess}")))
        asyncio.run(in_session("s2", _reusable("b", "SCN{say:sess}")))
        asyncio.run(in_session("s2", _contract("c", "SCN{say:sess}")))  # cache=False: a fresh run asked for
        assert len(stub.of("say", "sess")) == 3


class TestToolsStayWithinTheRole:
    def test_a_request_wider_than_the_role_is_refused(self, full):
        root, stub, _ = full()
        r = asyncio.run(root.subagent_orchestrator.run(
            _contract("r", "SCN{say:wide}", role="researcher", tools=["read_file", "write_file"])))
        assert not r.success and r.refusal_reason == "tools" and "write_file" in r.output
        assert not stub.of("say", "wide")

    def test_a_request_within_the_role_runs(self, full):
        root, stub, _ = full()
        r = asyncio.run(root.subagent_orchestrator.run(
            _contract("r", "SCN{say:narrow}", role="researcher", tools=["read_file", "grep"])))
        assert r.success and stub.of("say", "narrow")[-1]["tools"] == ["grep", "read_file"]


class TestSchemaFailureFailsTheChild:
    SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}

    def test_output_missing_a_required_field_fails(self, full):
        root, _, _ = full()
        r = asyncio.run(root.subagent_orchestrator.run(_contract(
            "s", "SCN{json:bad}", output_schema=self.SCHEMA, output_format="json", auto_retry_parse=False)))
        assert not r.success and "Schema validation failed" in r.error
        assert '"other"' in r.output, "the text the child produced is kept"
        [rec] = root.agent_registry.list()
        assert rec.status == "failed"

    def test_valid_output_still_succeeds(self, full):
        root, _, _ = full()
        r = asyncio.run(root.subagent_orchestrator.run(_contract(
            "s", "SCN{json:good}", output_schema=self.SCHEMA, output_format="json")))
        assert r.success and r.validated_output == {"verdict": "ok"}
