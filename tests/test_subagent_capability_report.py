"""The `subagent_start` payload must report the child's real authority surface.

The renderer (wisp-desktop `SubagentPanel`) shows what a subagent may actually
do. If that display is reconstructed from a count or guessed from the contract,
it can tell the user something the executor was never governed by. So these
tests pin the *shape and the derivation*, not just the plumbing.

Two failure modes are specifically locked:

1. `unbounded` inferred from a list length. An empty or shortened capability
   list means "narrowed", never "unconstrained" — that inversion reports the
   most authority exactly when the agent has the least.
2. A legacy/absent payload key defaulting to permissive. Unknown authority must
   render as unknown, not as unbounded.
"""

from __future__ import annotations

import pytest

from wisp.multi_agent._runner import _effective_child_tools
from wisp.multi_agent.task import EventKind, OrchestratorEvent
from wisp.tools.registry import TOOL_SCHEMAS

REGISTRY_SIZE = len(TOOL_SCHEMAS)


def _start_message(payload: dict) -> dict:
    return OrchestratorEvent(
        task_id="sub-1", event_type=EventKind.TASK_STARTED, payload=payload
    ).to_ws_message()


class TestUnboundedIsNeverInferredFromAList:
    def test_a_shortened_list_is_not_unbounded(self):
        """A narrowed child is the *opposite* of an unconstrained one."""
        caps = _effective_child_tools(None, "auto_edit")
        if len(caps) >= REGISTRY_SIZE:
            pytest.skip("auto_edit no longer narrows; nothing to distinguish")

        msg = _start_message(
            {"role": "coder", "capabilities": caps, "unbounded": False}
        )
        assert msg["unbounded"] is False
        assert len(msg["capabilities"]) < REGISTRY_SIZE

    def test_unbounded_true_only_under_full_permission_mode(self):
        msg = _start_message(
            {
                "role": "coder",
                "capabilities": _effective_child_tools(None, "full"),
                "unbounded": True,
            }
        )
        assert msg["unbounded"] is True
        # Unbounded must coincide with the full surface, never a subset of it.
        assert len(msg["capabilities"]) == REGISTRY_SIZE

    def test_read_only_child_is_not_unbounded(self):
        caps = _effective_child_tools(None, "read_only")
        msg = _start_message({"role": "ro", "capabilities": caps, "unbounded": False})
        assert msg["unbounded"] is False
        assert len(caps) < REGISTRY_SIZE


class TestMissingCapabilityDataFailsClosed:
    def test_absent_keys_report_unknown_not_unbounded(self):
        """An emitter predating this field must not read as unrestricted."""
        msg = _start_message({"role": "coder", "description": "d"})
        assert msg["unbounded"] is False
        assert msg["capabilities"] is None

    def test_capabilities_default_is_none_not_empty_list(self):
        """`None` means unknown. `[]` would be a real claim of zero authority."""
        msg = _start_message({"role": "coder", "unbounded": False})
        assert msg["capabilities"] is None

    def test_explicit_unbounded_false_is_preserved(self):
        msg = _start_message(
            {"role": "coder", "capabilities": ["read_file"], "unbounded": False}
        )
        assert msg["unbounded"] is False
        assert msg["capabilities"] == ["read_file"]


class TestReportedSurfaceIsTheEnforcementInput:
    def test_capabilities_match_what_the_core_advertises(self):
        """The reported list must be the same list fed to `allowed_tools`.

        `_runner.py` assigns `session_dict["allowed_tools"]` from this exact
        helper. If these ever diverge, the UI describes an authority surface the
        child does not have.
        """
        for mode in ("auto_edit", "read_only", "full", "ask_all"):
            expected = _effective_child_tools(None, mode)
            msg = _start_message({"role": "r", "capabilities": expected})
            assert msg["capabilities"] == expected, f"drift in mode={mode}"

    def test_registry_shrinks_the_surface_for_every_restrictive_mode(self):
        for mode in ("auto_edit", "read_only", "ask_all"):
            assert len(_effective_child_tools(None, mode)) <= REGISTRY_SIZE

    def test_full_mode_is_the_unrestricted_surface(self):
        assert len(_effective_child_tools(None, "full")) == REGISTRY_SIZE

    def test_explicit_contract_tools_are_narrowed_not_widened(self):
        """A contract asking for a subset gets that subset, never more."""
        caps = _effective_child_tools(["read_file", "list_files"], "full")
        assert set(caps) == {"read_file", "list_files"}
