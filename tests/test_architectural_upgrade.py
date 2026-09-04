"""Architectural upgrade suite — graph traps, RepoMap budgets, fuzzy
patching, subagent concurrency. All dependencies injected; no providers,
no servers, no network.
"""

from __future__ import annotations

import asyncio

import pytest


# ── 1. Execution graph: oscillation rollback + ceiling ─────────────────


def _mem_files(seed: dict[str, str]):
    store = dict(seed)
    return (lambda p: store.get(p)), (lambda p, c: store.__setitem__(p, c)), store


def test_graph_repeat_aborts_and_reverts_to_snapshot():
    from wisp.core.graph.loop import ExecutionGraph, PhaseResult
    from wisp.core.graph.phases import Phase

    read, write, store = _mem_files({"a.py": "v0\n"})

    async def _handler(phase, step):
        if phase is Phase.EXECUTE_SANDBOX:
            return PhaseResult(ok=True, diff="--- a\n+++ b\n@@ v1")
        return PhaseResult(ok=True)

    async def _go():
        # One linear run visits EXECUTE_SANDBOX once, so a repeat needs a
        # second turn through the same graph (trap state persists).
        graph = ExecutionGraph({p: _handler for p in Phase}, read, write)
        first = await graph.run(mutated_paths=["a.py"])
        second = await graph.run(mutated_paths=["a.py"])
        return first, second

    first, second = asyncio.run(_go())
    assert first.success is True
    outcome = second
    assert outcome.success is False
    assert outcome.artifact is not None
    assert outcome.artifact.reason == "oscillation_repeat"
    assert outcome.phase is Phase.RECOVER
    assert store["a.py"] == "v0\n"  # snapshot restored


def test_graph_two_cycle_across_runs_aborts_and_reverts():
    # The trap instance lives on the graph across run() calls (turn over
    # turn): d0, d1, d0 -> 2-cycle on the third run, with revert + RECOVER.
    from wisp.core.graph.loop import ExecutionGraph, PhaseResult
    from wisp.core.graph.phases import Phase

    read, write, store = _mem_files({"a.py": "v0\n"})
    script = iter(["diff-0", "diff-1", "diff-0"])

    async def _handler(phase, step):
        if phase is Phase.EXECUTE_SANDBOX:
            return PhaseResult(ok=True, diff=next(script))
        return PhaseResult(ok=True)

    async def _go():
        graph = ExecutionGraph({p: _handler for p in Phase}, read, write)
        first = await graph.run(mutated_paths=["a.py"])
        second = await graph.run(mutated_paths=["a.py"])
        third = await graph.run(mutated_paths=["a.py"])
        return first, second, third

    first, second, third = asyncio.run(_go())
    assert first.success and second.success
    assert third.success is False
    assert third.artifact is not None
    assert third.artifact.reason == "oscillation_cycle"
    assert third.phase is Phase.RECOVER
    assert store["a.py"] == "v0\n"


def test_oscillation_trap_two_cycle():
    from wisp.core.graph.loop import OscillationTrap, diff_hash

    trap = OscillationTrap()
    assert trap.observe(diff_hash("d0")) is None
    assert trap.observe(diff_hash("d1")) is None
    assert trap.observe(diff_hash("d0")) == "cycle"
    trap2 = OscillationTrap()
    assert trap2.observe(diff_hash("same")) is None
    assert trap2.observe(diff_hash("same")) == "repeat"


def test_graph_normal_run_has_no_artifact():
    from wisp.core.graph.loop import ExecutionGraph, PhaseResult
    from wisp.core.graph.phases import Phase

    async def _ok(phase, step):
        return PhaseResult(ok=True)

    async def _go():
        read, write, _ = _mem_files({})
        graph = ExecutionGraph({p: _ok for p in Phase}, read, write)
        return await graph.run()

    outcome = asyncio.run(_go())
    assert outcome.phase is Phase.TERMINATE
    assert outcome.success is True
    assert outcome.artifact is None


def test_graph_ceiling_artifact_on_forced_loop(monkeypatch):
    from wisp.core.graph import loop as loop_mod
    from wisp.core.graph.loop import ExecutionGraph, PhaseResult
    from wisp.core.graph.phases import Phase, next_phase as _real_next

    async def _ok(phase, step):
        return PhaseResult(ok=True)

    # Force REDUCE to cycle back to INIT so only the ceiling can stop us.
    def _cyclic(phase: Phase) -> Phase:
        return Phase.INIT if phase is Phase.REDUCE else _real_next(phase)

    monkeypatch.setattr(loop_mod, "next_phase", _cyclic)

    async def _go():
        read, write, _ = _mem_files({})
        graph = ExecutionGraph({p: _ok for p in Phase}, read, write, max_iterations=25)
        return await graph.run()

    outcome = asyncio.run(_go())
    assert outcome.success is False
    assert outcome.artifact is not None
    assert outcome.artifact.reason == "iteration_budget_exhausted"
    assert outcome.iterations_used == 25


def test_graph_approval_denial_parks_cleanly():
    from wisp.core.graph.loop import ExecutionGraph, PhaseResult
    from wisp.core.graph.phases import Phase

    async def _handler(phase, step):
        if phase is Phase.AWAIT_APPROVAL:
            return PhaseResult(ok=False, detail="user denied")
        return PhaseResult(ok=True)

    async def _go():
        read, write, _ = _mem_files({})
        graph = ExecutionGraph({p: _handler for p in Phase}, read, write)
        return await graph.run()

    outcome = asyncio.run(_go())
    assert outcome.success is True and outcome.phase is Phase.TERMINATE
    assert outcome.artifact is None


# ── 2. RepoMap: token budget + seeded ranking ──────────────────────────


def _repo(tmp_path, files: dict[str, str]):
    for rel, content in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return str(tmp_path)


def test_repomap_budget_fit_and_determinism(tmp_path):
    from wisp.core.context.repomap import RepoMap

    ws = _repo(tmp_path, {
        "auth.py": "def login(user):\n    return token(user)\n\ndef token(user):\n    return 'x'\n",
        "main.py": "from auth import login\n\ndef main():\n    login('a')\n    login('b')\n",
        "util.py": "def helper():\n    return 1\n",
    })
    repo = RepoMap(ws, budget_tokens=1024)
    first = repo.rank(["main.py"])
    second = repo.rank(["main.py"])
    assert [s.name for s in first] == [s.name for s in second]
    # Seeded file's neighborhood outranks the isolated module.
    names = [s.name for s in first]
    assert names.index("login") < names.index("helper")
    text = repo.format_for_llm(first)
    assert len(text) // 4 <= 1024
    assert "login" in text


def test_repomap_empty_workspace():
    from wisp.core.context.repomap import RepoMap

    assert RepoMap("/nonexistent-ws-xyz").rank([]) == []
    assert RepoMap("/nonexistent-ws-xyz").format_for_llm([]) == ""


# ── 3. Search/replace fuzzy patching ───────────────────────────────────


def test_search_replace_exact_and_whitespace_tiers():
    from wisp.core.mutator.search_replace import (
        MatchTier, apply_block, parse_block,
    )

    content = "def f():\n    x = 1\n    return x\n"
    block = parse_block("<<<<<<< SEARCH\n    x = 1\n=======\n    x = 2\n>>>>>>> REPLACE\n")
    patched, tier = apply_block(content, block)
    assert tier is MatchTier.EXACT
    assert "x = 2" in patched

    shifted = "def f():\n        x   =   1\n        return x\n"
    patched2, tier2 = apply_block(shifted, block)
    assert tier2 is MatchTier.WHITESPACE
    assert "x = 2" in patched2


def test_search_replace_similar_tier_and_failure_is_disk_safe(tmp_path):
    from wisp.core.mutator.search_replace import (
        MatchTier, SearchReplaceError, apply_block, parse_block,
    )

    content = "def calclate_total(items):\n    return sum(items)\n"
    block = parse_block("<<<<<<< SEARCH\ndef calculate_total(items):\n=======\ndef total(items):\n>>>>>>> REPLACE\n")
    patched, tier = apply_block(content, block)
    assert tier is MatchTier.SIMILAR
    assert "def total(items):" in patched

    victim = tmp_path / "victim.py"
    victim.write_text("unrelated content\n")
    with pytest.raises(SearchReplaceError):
        apply_block("totally different\ntext here\n", block)
    assert victim.read_text() == "unrelated content\n"


def test_search_replace_malformed_block():
    from wisp.core.mutator.search_replace import SearchReplaceError, parse_block

    with pytest.raises(SearchReplaceError):
        parse_block("no fences here\n")
    with pytest.raises(SearchReplaceError):
        parse_block("<<<<<<< SEARCH\n=======\nx\n>>>>>>> REPLACE\n")


# ── 4. Subagent concurrency + schema (new hardened package) ────────────


def test_subagent_semaphore_bound():
    from wisp.core.subagent.coordinator import Coordinator, CoordinatorConfig
    from wisp.core.subagent.protocol import ExecutionPolicy, TaskFrame

    in_flight = 0
    peak = 0

    async def _worker(frame: TaskFrame, emit) -> dict:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {"task_id": frame.task_id, "status": "SUCCESS",
                "findings": [], "token_usage": {"prompt": 1, "completion": 1}}

    async def _go():
        coord = Coordinator(
            worker_fn=_worker,
            config=CoordinatorConfig(
                default_policy=ExecutionPolicy(max_concurrent=4, timeout_s=60.0)))
        frames = [coord.build_frame(f"t{i}", role="explorer") for i in range(12)]
        return await coord.fanout(frames)

    reduced = asyncio.run(_go())
    assert 1 < peak <= 4
    assert reduced.succeeded == 12


def test_subagent_result_schema_rejects_prose():
    from pydantic import ValidationError

    from wisp.core.subagent.protocol import SubagentResult

    with pytest.raises(ValidationError):
        SubagentResult.model_validate({"task_id": "t", "status": "SUCCESS",
                                       "findings": "it looks good overall"})


def test_compactor_micro_tier_preserves_pairs():
    from wisp.core.context.compactor import CLEARED_MARKER, micro_compact

    messages = [{"role": "user", "content": "go"}]
    for i in range(6):
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"c{i}"}]})
        messages.append({"role": "tool", "name": "read_file",
                         "tool_use_id": f"c{i}", "content": "body" * 500})
    report = micro_compact(messages, keep_turns=3)
    assert report.changed
    cleared = [m for m in messages if m.get("content") == CLEARED_MARKER]
    assert len(cleared) == 3
    # Pair schema intact: every cleared body keeps its tool_use_id.
    assert all("tool_use_id" in m for m in cleared)
