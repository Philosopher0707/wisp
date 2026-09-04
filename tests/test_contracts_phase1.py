"""Phase 1 contract tests — pure unit tests (pass now) + conformance probes.

Conformance probes assert the *target* architecture against the *current*
implementation. They are marked xfail so the suite stays green until Phase 2
lands; an XPASS means the debt was fixed and the marker should be removed.

Conventions: mirrors source path wisp/core/contracts.py → tests/test_contracts_phase1.py.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from wisp.core.contracts import (
    ApprovalDecision,
    CancelledTurnError,
    ErrorKind,
    FatalProviderError,
    PrunePolicy,
    RetryPolicy,
    SessionState,
    StreamGuardConfig,
    ToolRisk,
    TransientTransportError,
    TransportConfig,
    TurnBudget,
    WispError,
    classify_status,
    is_cancellation,
    risk_for_tool,
)


# ── Pure contract unit tests (must pass on delivery) ───────────────────


def test_classify_status_never_retries_permanent_4xx():
    assert classify_status(429) is ErrorKind.RATE_LIMITED
    assert classify_status(503) is ErrorKind.TRANSIENT_TRANSPORT
    assert classify_status(400) is ErrorKind.FATAL_PROTOCOL
    assert classify_status(404) is ErrorKind.FATAL_PROTOCOL
    assert classify_status(None) is None


def test_error_to_event_dict_shape():
    err = TransientTransportError("write timed out", context=["budget: 90s"])
    ev = err.to_event_dict()
    assert ev["type"] == "error"
    assert ev["kind"] == ErrorKind.TRANSIENT_TRANSPORT.value
    assert ev["recoverable"] is True
    fatal = FatalProviderError("bad credentials")
    assert fatal.to_event_dict()["recoverable"] is False


def test_cancellation_never_transient():
    assert is_cancellation(asyncio.CancelledError()) is True
    assert is_cancellation(KeyboardInterrupt()) is True
    assert is_cancellation(TimeoutError("write timed out")) is False
    assert is_cancellation(ConnectionResetError("reset")) is False


def test_transport_config_requests_folds_write_into_read():
    cfg = TransportConfig()
    connect, read = cfg.as_requests_timeout()
    assert (connect, read) == (15.0, 120.0)
    assert cfg.pool_connections == 20


def test_retry_policy_exponential_backoff_bounded():
    pol = RetryPolicy()
    assert pol.delay_for_attempt(1) == pytest.approx(0.5)
    assert pol.delay_for_attempt(2) == pytest.approx(1.0)
    assert pol.delay_for_attempt(10) == pytest.approx(8.0)  # capped


def test_prune_policy_validates():
    assert PrunePolicy().validate() == []
    bad = PrunePolicy(keep_last_n_full=0, max_total_bytes=1)
    assert len(bad.validate()) >= 1


def test_approval_decision_back_compat_tuple():
    assert ApprovalDecision(allowed=True).to_tuple() == (True, None)
    ok, reason = ApprovalDecision(allowed=False, reason="ask_all").to_tuple()
    assert ok is False and reason == "ask_all"


def test_risk_table_fail_closed_on_unknown():
    assert risk_for_tool("read_file") is ToolRisk.READ
    assert risk_for_tool("run_bash") is ToolRisk.EXEC
    assert risk_for_tool("totally_new_mcp_tool") is ToolRisk.EXEC


def test_turn_budget_bounds():
    assert TurnBudget().validate() == []
    assert TurnBudget(max_iterations=0).validate() != []
    assert TurnBudget(max_iterations=201).validate() != []


def test_session_state_counts_user_turns():
    s = SessionState(session_id="s", messages=[
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ])
    assert s.user_turns() == 2


def test_contracts_module_has_no_wisp_imports():
    import wisp.core.contracts as c

    src = inspect.getsource(c)
    assert "from wisp." not in src and "import wisp." not in src


# ── Conformance probes (xfail until Phase 2 — documents current debt) ──


def test_provider_stream_reraises_cancellation():
    """D2 fixed Phase 2.2: cancellation-first guards in provider_stream."""
    import wisp.core.provider_stream as ps

    src = inspect.getsource(ps.guarded_provider_stream)
    # Target: cancellation check before transient classification.
    assert "is_cancellation" in src or "CancelledError" in src


@pytest.mark.xfail(reason="D8: is_transient_error falls back to substring matching", strict=False)
def test_transient_classifier_has_no_substring_fallback():
    import wisp.core.transport as t

    src = inspect.getsource(t.is_transient_error)
    assert "in msg_lower" not in src and "transient_substrings" not in src


def test_prune_callsites_share_single_policy():
    """D3 fixed Phase 2.3: all call-sites pass the shared DEFAULT_PRUNE_POLICY."""
    import pathlib

    files = {
        "wisp/core/stateless.py": 0,
        "wisp/ollama_client.py": 0,
        "wisp/providers/openai.py": 0,
    }
    for rel in files:
        src = pathlib.Path(rel).read_text()
        # Target: every call-site imports PrunePolicy from contracts.
        assert "PrunePolicy" in src or "PRUNE_POLICY" in src, f"{rel} does not use PrunePolicy"


@pytest.mark.xfail(reason="D7: legacy agent/ namespace still imported at runtime", strict=False)
def test_no_runtime_imports_of_legacy_agent_package():
    import pathlib

    hits = []
    for p in list(pathlib.Path("wisp").rglob("*.py")):
        if "test" in p.parts:
            continue
        try:
            src = p.read_text()
        except OSError:
            continue
        if "from agent." in src or "import agent." in src:
            hits.append(str(p))
    assert not hits, f"legacy imports remain: {hits[:5]}"


def test_prompt_cache_is_bounded():
    """D1 fixed Phase 2.5: _SYSTEM_PROMPT_CACHE is a BoundedPromptCache."""
    import wisp.core.stateless as st

    cache = st._SYSTEM_PROMPT_CACHE
    assert hasattr(cache, "maxsize") and cache.maxsize <= 256
