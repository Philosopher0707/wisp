"""G0 replay fixtures: 7 representative bundles, schema/redaction/hash checked.

The partial/truncated case is the FUTURE schema state (runtime does not yet
persist truncation markers — P1-3): the fixture proves the schema can carry
it, and is marked accordingly.
"""
from __future__ import annotations

from tests.reliability.replay import (BUNDLE_VERSION, make_bundle,
                                      round_trip, validate)

BASE = dict(wisp_version="g0-test", commit_sha="c52edc2-test",
            provider="mock", model="mock-model",
            model_configuration={"temperature": 0, "deadlines": [90, 90]},
            execution_configuration={"timeout_s": 300, "max_attempts": 3},
            observed_decisions=[{"gate": "approval", "verdict": "allow"}],
            timestamps={"start": 1.0, "end": 2.0})


def _graph_fields(status="succeeded"):
    return dict(
        graph_hash="abcd1234",
        graph_definition={"id": "g", "nodes": [{"id": "a"}]},
        policy_fingerprint="pol1",
        node_attempts={"a": 1}, retry_information=[],
        timeout_information=[], cancellation_information=[],
        stream_completion_state="complete", truncation_state="complete",
        deterministic=status == "succeeded")


def test_fixture_successful_graph_run():
    b = make_bundle(request="ship it",
                    artifact_references=["art://1"],
                    artifact_hashes=["00" * 32],
                    audit_head="ff" * 32, **BASE, **_graph_fields())
    assert validate(b) == [], validate(b)
    round_trip(b)


def test_fixture_failed_graph_run():
    f = _graph_fields(status="failed")
    f.update(node_attempts={"a": 2},
             retry_information=[{"node": "a", "reason": "FAILURE"}])
    b = make_bundle(request="ship it", audit_head="ff" * 32, **BASE, **f)
    assert validate(b) == [], validate(b)
    round_trip(b)


def test_fixture_cancelled_run():
    f = _graph_fields(status="cancelled")
    f.update(cancellation_information=[{"node": "a", "at": "running"}])
    b = make_bundle(request="stop", audit_head="ff" * 32, **BASE, **f)
    assert validate(b) == [], validate(b)
    round_trip(b)


def test_fixture_timeout():
    f = _graph_fields(status="failed")
    f.update(timeout_information=[{"node": "a", "timeout_s": 0.05}],
             truncation_state="stalled", deterministic=False)
    b = make_bundle(request="slow job", audit_head="ff" * 32, **BASE, **f)
    assert validate(b) == [], validate(b)
    round_trip(b)


def test_fixture_workspace_mutation():
    b = make_bundle(request="edit a.py", workspace_snapshot="snap1",
                    changesets=["cs-01"], workspace_journal="completed:1",
                    audit_head="ff" * 32, **BASE)
    assert validate(b) == [], validate(b)
    round_trip(b)


def test_fixture_provider_failure():
    b = make_bundle(request="ask",
                    retry_information=[{"attempt": 1, "reason": "status-503"},
                                       {"attempt": 2, "reason": "status-503"}],
                    stream_completion_state="error", truncation_state="UNKNOWN",
                    deterministic=False, audit_head="ff" * 32, **BASE)
    assert validate(b) == [], validate(b)
    round_trip(b)


def test_fixture_partial_truncated_future_schema():
    """FUTURE state: runtime persists no truncation marker yet (P1-3).

    Fixture proves the g0.1 schema carries it when G1 starts emitting it.
    """
    b = make_bundle(request="long answer",
                    stream_completion_state="truncated",
                    truncation_state="truncated", deterministic=False,
                    schema_state="future: requires P1-3 runtime markers",
                    audit_head="ff" * 32, **BASE)
    assert validate(b) == [], validate(b)
    round_trip(b)
    assert b["truncation_state"] == "truncated"


def test_fixture_secret_is_rejected():
    # sk-ant key must be redacted at build time (redact_record, recursive);
    # a RAW bundle bypassing make_bundle must fail validation.
    b = make_bundle(request="use key sk-ant-testkey1234567890abcdef",
                    audit_head="ff" * 32, **BASE)
    assert "sk-ant-testkey" not in b["request"], b["request"]
    assert validate(b) == [], validate(b)
    raw = dict(b)
    raw["request"] = "use key sk-ant-testkey1234567890abcdef"
    errs = validate(raw)
    assert any("secret scan" in e for e in errs), errs


def test_fixture_missing_required_rejected():
    assert validate({"bundle_version": BUNDLE_VERSION}) != []
