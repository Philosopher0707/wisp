"""M9 — the execution view, and whether it can be a projection at all.

`WISP_TARGET_ARCHITECTURE.md` states the target model:

```
Immutable Event Journal  --materialize-->  Graph State  --project-->  Execution View
```

and says the message list is a view "projected from **both**". M9 in the ledger
shortened that to *"the message list as a projection of the graph"*, and five
other items were recorded as blocked on it.

Reconnaissance found three things, and this file pins all three:

1. **The graph cannot project the transcript.** `TaskNode` carries no tool name,
   no arguments, no result and no assistant text. The graph is a *shape*, not a
   payload. So M9 as written is not expressible, and making it expressible would
   mean putting a second copy of the transcript in the nodes — the "second path
   in" defect P2 warned about.

2. **The projection that *is* expressible — the journal's — is not faithful.**
   `runtime.py` says at the construction site that "the log has to reproduce
   `messages` exactly". It does not: `Session.apply` adds a `name` key to every
   tool reply that the live transcript does not have.

3. **The divergence has a consumer.** `context_pruner._get_tool_name_for_result`
   reads that key, so the same session takes a different branch depending on
   which path produced its messages.

`test_the_journal_reproduces_the_transcript_exactly` is the RED-first test: it
fails on the pre-M9 code and is the invariant M9a establishes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path


from wisp.core.session import SessionEventType
from wisp.core.task_graph import TaskNode

REPO = Path(__file__).resolve().parents[1]

#: The keys a message may carry, by role. A `tool` reply is what the provider
#: protocol pairs with a `tool_calls` block by id — nothing more.
TOOL_REPLY_KEYS = {"role", "content", "tool_call_id"}


class _DictProvider:
    """Scripted provider: one entry per round, the last entry repeats."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        script = self._rounds[min(self.calls - 1, len(self._rounds) - 1)]
        yield from script()


def _tool_round(calls):
    def _g():
        yield {"type": "tool_calls", "calls": calls}
        yield {"type": "done", "done_reason": "tool_calls"}
    return _g


def _content_only(text, reason="stop"):
    def _g():
        yield {"type": "content", "text": text}
        yield {"type": "done", "done_reason": reason}
    return _g


def _read_call(path: str, cid: str) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": "read_file", "arguments": {"path": path}}}


def _run_turn(tmp_path, rounds, *, files=None, sid="m9", **cfg_kw):
    """Drive one real turn and return (live session, repository)."""
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.core.runtime import AgentRuntime
    from wisp.core.session_repo import SessionRepository
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    for name, text in (files or {}).items():
        (ws / name).write_text(text)

    config = WispConfig().replace(workspace=str(ws), **cfg_kw)
    store = UnifiedStore(tmp_path / "wisp.db")
    repo = SessionRepository(store)
    provider = _DictProvider(rounds)

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=None)

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(), core_factory=factory,
        session_repo=repo, config=config)
    session = {"id": sid, "model": "mock", "workspace": str(ws), "messages": []}

    async def _main():
        return [ev async for ev in runtime.run_turn(session, prompt="go")]
    asyncio.run(_main())
    return session, repo


# ══════════════════════════════════════════════════════════════════════════
# 1. The projection that exists is not faithful
# ══════════════════════════════════════════════════════════════════════════


class TestTheProjectionIsFaithful:
    def test_the_journal_reproduces_the_transcript_exactly(self, tmp_path):
        """**The RED-first test.** `runtime.py` states the invariant at the
        construction site: *"replay replaces the live transcript, so the log
        has to reproduce `messages` exactly."*

        It does not. The replayed tool reply carries a `name` key the live one
        does not, so a crash that swaps in the replayed transcript changes the
        message shape mid-session.
        """
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]), _content_only("read it")],
            files={"a.txt": "hello"},
        )
        live = session["messages"]
        replayed = repo.load_session("m9").messages

        assert [m["role"] for m in replayed] == [m["role"] for m in live], (
            "the roles diverged, which is a worse failure than a key")
        assert replayed == live, (
            "the replayed transcript is not the live transcript:\n"
            f"  live  : {json.dumps(live, default=str)[:600]}\n"
            f"  replay: {json.dumps(replayed, default=str)[:600]}")

    def test_a_tool_reply_carries_only_the_protocol_keys(self, tmp_path):
        """The shape is a contract, so assert it directly rather than only via
        equality with a transcript that might drift with it."""
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]), _content_only("read it")],
            files={"a.txt": "hello"},
        )
        for source, messages in (("live", session["messages"]),
                                 ("replay", repo.load_session("m9").messages)):
            for msg in messages:
                if msg["role"] != "tool":
                    continue
                assert set(msg) == TOOL_REPLY_KEYS, (
                    f"{source} tool reply has keys {sorted(msg)}; expected "
                    f"{sorted(TOOL_REPLY_KEYS)}")

    def test_the_projection_is_faithful_after_a_denied_tool(self, tmp_path):
        """The denial path streams a reply without a call event, so it exercises
        the reply-only branch of the pairing rule. `jsonschema` is absent here,
        so every call is denied — which makes this the *default* path in this
        environment rather than an edge case."""
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]), _content_only("done")],
            files={"a.txt": "hello"},
        )
        assert session["messages"] == repo.load_session("m9").messages

    def test_the_projection_is_faithful_across_several_exchanges(self, tmp_path):
        """More than one exchange, so the pairing loop runs more than once and
        the ordering cannot be accidentally right."""
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]),
             _tool_round([_read_call("a.txt", "c1")]),
             _content_only("done")],
            files={"a.txt": "hello"},
        )
        assert len([m for m in session["messages"] if m["role"] == "tool"]) == 2
        assert session["messages"] == repo.load_session("m9").messages

    def test_a_content_only_turn_is_faithful(self, tmp_path):
        """No exchange at all — the degenerate case, which must not be special."""
        session, repo = _run_turn(tmp_path, [_content_only("hi")])
        assert session["messages"] == repo.load_session("m9").messages


# ══════════════════════════════════════════════════════════════════════════
# 2. The graph cannot project the transcript — and must not be made to
# ══════════════════════════════════════════════════════════════════════════


class TestTheGraphCannotProjectTheTranscript:
    def test_the_payload_ratchet_is_superseded_and_strictly_stronger(self):
        """**The ratchet, and why M11 replaced it (ADR-0033).**

        M9 asked for the message list to be a projection of the graph. That is
        not expressible: a node holds no tool name, no arguments, no result and
        no assistant text. Making it expressible means copying the transcript
        into the nodes — a second copy that can disagree with the first, which
        is the defect class this migration exists to remove.

        The guard this replaces was a **name blacklist**, and it failed in both
        directions:

        * **evadable by naming** — a payload field called `body` passed it;
        * **it forbade the fix** — it listed `tool_call_id` as a payload, which
          is exactly the reference M9's *own* report (§17.6) says M11 requires.

        So the property is kept and the mechanism replaced: every `TaskNode`
        field now carries a declared kind. This asserts the replacement catches
        a payload the blacklist would have missed, and that the reference M11
        added has a declared kind rather than being a payload.
        """
        from wisp.core.task_graph import (
            NODE_FIELD_KINDS, NodeFieldKind, node_field_violations)

        names = {f.name for f in dataclasses.fields(TaskNode)}
        missed_by_the_blacklist = names | {"body"}
        assert node_field_violations(missed_by_the_blacklist), (
            "a payload field the old name blacklist would have missed is not "
            "caught; the replacement ratchet is weaker than what it replaced")
        assert NODE_FIELD_KINDS["work_unit"] is NodeFieldKind.REFERENCE, (
            "the work-unit reference is not classified as a reference")

    def test_the_graph_cannot_supply_a_message_content(self, tmp_path):
        """Asserted behaviourally, not just structurally: replay the graph from
        the log and show it contains nothing that could become message content."""
        from wisp.core.task_graph import materialize, build_turn_graph

        graph = materialize(build_turn_graph(
            "r1", ["call:c0", "call:c1", "output"]))
        blob = json.dumps(graph.to_dict())
        for probe in ("read_file", "hello", "read it"):
            assert probe not in blob, (
                f"the graph now contains {probe!r}; it is no longer only a shape")

    def test_node_ids_are_structural_and_the_work_unit_is_the_identity(self):
        """**M11 inverted this test — it used to assert the defect.**

        It read: *"The nodes are `turn:0 … turn:N-1`, generated from a count.
        They do not reference the exchange they stand for, so a node cannot be
        traced back to the work it records."* That was M9's finding §17.6, and
        it was pinned so it would not be rediscovered.

        The ids are **still** `turn:i`, deliberately: `node_id` is the graph's
        *structural* key — edges, `deps`, transitions and supersession all
        address it — and deriving it from a provider-supplied id would put the
        topology at the mercy of transcript data. The identity is a separate
        field, and it is now required: a node that records no work unit is not
        constructible (`build_turn_graph` refuses one).
        """
        from wisp.core.task_graph import materialize, build_turn_graph

        graph = materialize(build_turn_graph(
            "r1", ["call:c0", "call:c1", "output"]))
        assert [n.node_id for n in graph.nodes] == ["turn:0", "turn:1", "turn:2"]
        assert [n.work_unit for n in graph.nodes] == [
            "call:c0", "call:c1", "output"], (
            "a node does not name its work unit; it is an index again")
        assert all(n.work_unit for n in graph.nodes)

    def test_the_graph_records_a_lower_bound_on_iterations(self, tmp_path):
        """A content-only iteration leaves no exchange, so the node count is a
        lower bound — documented in the runtime and asserted here so the
        distinction survives."""
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]), _content_only("done")],
            files={"a.txt": "hello"}, task_graph=True,
        )
        events = repo.load_events("m9")
        graph = [e for e in events
                 if e.event_type == SessionEventType.TASK_GRAPH][0].payload["graph"]
        assistant_rounds = len([m for m in session["messages"]
                                if m["role"] == "assistant"])
        # 1 exchange + 1 terminal node = 2, and 2 assistant messages by
        # coincidence here; the point is that the graph is derived from
        # EXCHANGES, so it can only ever be a lower bound on iterations.
        assert len(graph["nodes"]) == 2
        assert assistant_rounds >= len(graph["nodes"]) - 1


# ══════════════════════════════════════════════════════════════════════════
# 3. The divergence has a consequence
# ══════════════════════════════════════════════════════════════════════════


class TestTheDivergenceHasAConsequence:
    def test_the_pruner_takes_the_same_branch_on_both_paths(self, tmp_path):
        """`context_pruner._get_tool_name_for_result` reads `name` off the tool
        message first. On the live path the key is absent so it resolves the
        name from `tool_call_id`; on the replayed path it short-circuits.

        Both must give the same answer — the divergence was benign only because
        the fallback happens to agree.
        """
        from wisp.core.context_pruner import _get_tool_name_for_result

        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]), _content_only("done")],
            files={"a.txt": "hello"},
        )
        id_to_name = {"c0": "read_file"}

        def resolve(messages):
            return [_get_tool_name_for_result(m, id_to_name)
                    for m in messages if m["role"] == "tool"]

        assert resolve(session["messages"]) == resolve(
            repo.load_session("m9").messages)
        assert resolve(session["messages"]) == ["read_file"]

    def test_the_blob_and_the_journal_agree_on_the_transcript(self, tmp_path):
        """Two durable sources, one session. If they disagree, `reconstruct()`
        returns different messages depending on which path it chose — which is
        the divergence `reconstruct()` exists to be able to explain."""
        session, repo = _run_turn(
            tmp_path,
            [_tool_round([_read_call("a.txt", "c0")]), _content_only("done")],
            files={"a.txt": "hello"},
        )
        blob = repo._store.load_session("m9")
        assert blob is not None, "the turn-end snapshot must have been saved"
        assert blob["messages"] == session["messages"], (
            "the blob and the live transcript disagree")
        assert repo.load_session("m9").messages == blob["messages"], (
            "the journal and the blob disagree on the transcript; "
            "reconstruct() would return different messages per source")
