"""Phase 13-J diagnostic tests (FORENSIC ONLY — pins current behavior).

Each test documents a proven incident property. All pass against the
UNFIXED code; a future repair phase must flip the .*_should_.* assertions
deliberately, not accidentally.
"""
from __future__ import annotations

import pytest


def _fanout_args(**over):
    tasks = [{"task": "summarize a.py", "role": "researcher",
              "model": None}]
    args = {"tasks": tasks, "max_concurrent": 3, "mode": "background"}
    args.update(over)
    return args


def _core(provider, permission_mode="full"):
    from wisp.core.engine import WispAgentCore
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import PermissionMode, SecurityPolicy
    return WispAgentCore(
        provider=provider,
        security=SecurityPolicy(
            permission_mode=PermissionMode(permission_mode)),
        extensions=ExtensionHost(),
    )


class _MockProvider:
    def __init__(self, responses):
        self.responses = responses

    def generate_stream_events(self, system_prompt, messages, tools=None,
                               checkpoint_every=50):
        for resp in self.responses:
            yield resp


class TestModelNullContract:
    """Incident §0: explicit null fails validation though runtime handles it."""

    def _error(self, args):
        core = _core(_MockProvider([]))
        return core._validate_tool_args("fanout", args)

    def test_explicit_null_model_accepted(self):
        # 13-J1 FIXED (incident §0): explicit null means inherit-parent,
        # matching runtime semantics. Renamed from ..._rejected.
        err = self._error(_fanout_args())
        assert err is None

    def test_omitted_model_accepted(self):
        tasks = [{"task": "summarize a.py", "role": "researcher"}]
        assert self._error({"tasks": tasks}) is None

    def test_malformed_model_rejected(self):
        tasks = [{"task": "x", "model": 123}]
        assert self._error({"tasks": tasks}) is not None

    def test_explicit_model_accepted(self):
        tasks = [{"task": "x", "model": "qwen2.5"}]
        assert self._error({"tasks": tasks}) is None

    def test_empty_tasks_rejected(self):
        # 13-J1 FIXED: minItems 1 moved emptiness into the schema contract.
        assert self._error({"tasks": []}) is not None

    def test_duplicate_tasks_pass_schema(self):
        t = {"task": "x"}
        assert self._error({"tasks": [t, dict(t)]}) is None

    def test_max_concurrent_unbounded_in_schema(self):
        from wisp.tools.registry import TOOL_SCHEMAS
        fn = next(s for s in TOOL_SCHEMAS
                  if s["function"]["name"] == "fanout")["function"]
        assert "maximum" not in fn["parameters"]["properties"][
            "max_concurrent"]

    def test_contract_accepts_none_model(self):
        # Runtime side already tolerates what the schema rejects.
        from wisp.multi_agent.task import SubagentContract
        c = SubagentContract(name="f", task="x", model=None)
        assert c.model is None


class TestInvalidReachesApproval:
    """Incident §3 (13-J1 FIXED): validation precedes Gate1 approval."""

    async def _run(self, tmp_path, decision, args=None):
        seen = []

        # Core-level handler contract is handler(event) -> bool
        # (approval_gate.py:109); the (name, args, reason) shape is
        # executor-level only.
        async def _handler(event):
            seen.append((event.get("name"), event.get("arguments")))
            return decision

        # AUTO_EDIT: fanout is EXEC-risk so the gate must consult approval.
        core = _core(_MockProvider([
            {"type": "tool_call", "name": "fanout",
             "arguments": args if args is not None else _fanout_args(),
             "id": "call_1"},
            {"type": "done"},
        ]), permission_mode="auto_edit")
        session = {"id": "j", "messages": [], "model": "m",
                   "workspace": str(tmp_path)}
        events = [e async for e in core.turn(
            session, "analyze", approval_handler=_handler)]
        return seen, events

    @pytest.mark.asyncio
    async def test_denied_invalid_call_consults_handler(self, tmp_path):
        # 13-J1 FIXED behavior (was: invalid reached approval). model:null
        # is now valid, so approval IS consulted and the denial is lawful.
        seen, events = await self._run(tmp_path, False)
        assert seen, "approval UI never saw the valid call"
        name, args = seen[0]
        assert name == "fanout"
        assert args["tasks"][0]["model"] is None  # valid by inheritance
        assert "Schema validation failed" not in str(events)

    @pytest.mark.asyncio
    async def test_allowed_invalid_call_fails_validation(self, tmp_path):
        # 13-J1 FIXED: still-invalid payload (model:123) fails validation
        # with approval never consulted and the id preserved.
        seen, events = await self._run(
            tmp_path, True,
            args={"tasks": [{"task": "x", "model": 123}],
                  "max_concurrent": 2, "mode": "blocking"})
        assert seen == []
        blob = str(events)
        assert "Schema validation failed" in blob
        assert "call_1" in blob  # tool_call_id preserved
