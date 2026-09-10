"""12.5E: one redaction semantic across audit sinks.

THREAT: secret-bearing values under innocent keys, nested structures,
bytes, or metadata reaching durable audit storage raw.
EXPECTED: every sink funnels through auth.secrets patterns; stores keep
their separation (JSONL session log vs SQLite immutable trail).
"""

from __future__ import annotations

import pytest

SECRETS = [
    "OPENAI_API_KEY=sk-probe-1234567890abcdef",
    "Authorization: Bearer probe-token-abcdef123456",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_probetoken1234567890abcdef",
    "password: hunter2-hunter2",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIBPROBE\n-----END RSA PRIVATE KEY-----",
]


def _trail(tmp_path):
    from wisp.infra.audit import AuditTrail
    from pathlib import Path
    return AuditTrail(path=Path(str(tmp_path)) / "audit.jsonl")


class TestInvariant:
    @pytest.mark.parametrize("secret", SECRETS)
    def test_value_under_innocent_key(self, tmp_path, secret):
        trail = _trail(tmp_path)
        trail.record("test", key="notes", old_value=f"prefix {secret} suffix")
        blob = open(trail._path, errors="replace").read()
        assert secret not in blob
        assert "MIIBPROBE" not in blob

    @pytest.mark.parametrize("secret", SECRETS)
    def test_nested_structures(self, tmp_path, secret):
        trail = _trail(tmp_path)
        trail.record("test", key="config",
                     new_value={"a": [secret], "b": {"c": secret}})
        blob = open(trail._path, errors="replace").read()
        assert secret not in blob

    def test_metadata_scrubbed(self, tmp_path):
        trail = _trail(tmp_path)
        trail.record("test", metadata={"token": "ghp_probetoken1234567890abcdef"})
        blob = open(trail._path, errors="replace").read()
        assert "ghp_probe" not in blob

    def test_bytes_decoded_and_scrubbed(self, tmp_path):
        trail = _trail(tmp_path)
        trail.record("test", key="blob", new_value=b"password: hunter2-hunter2")
        blob = open(trail._path, errors="replace").read()
        assert "hunter2" not in blob

    def test_malformed_values_never_crash(self, tmp_path):
        trail = _trail(tmp_path)
        trail.record("test", key="x", new_value=object())
        trail.record("test", key="y", new_value={"a": object()})
        assert trail.verify() is None

    def test_mixed_case_keys_still_masked(self, tmp_path):
        trail = _trail(tmp_path)
        trail.record("test", key="Api-Key", new_value="sk-probe-1234567890abcdef")
        blob = open(trail._path, errors="replace").read()
        assert "sk-probe" not in blob

    def test_chain_still_verifies(self, tmp_path):
        trail = _trail(tmp_path)
        for i in range(5):
            trail.record(f"action-{i}", key="k", new_value={"n": i})
        assert trail.verify() is None

    def test_tools_audit_funnel_uses_canonical_patterns(self):
        import inspect
        import wisp.tools.audit as ta
        src = inspect.getsource(ta._redacted_summary)
        assert "wisp.auth.secrets" in src

    def test_graph_scrub_funnel_uses_canonical_patterns(self):
        import inspect
        import wisp.graph.security as gs
        src = inspect.getsource(gs.scrub_text)
        assert "auth.secrets" in src or "redact" in src
