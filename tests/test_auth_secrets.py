# tests/test_auth_secrets.py
from wisp.auth.secrets import redact, redact_record, scan_for_secrets


def test_redact_aws_key():
    s = "key = AKIAIOSFODNN7EXAMPLE"
    assert "AKIAIOSFODNN7EXAMPLE" not in redact(s)
    assert "[REDACTED:aws-access-key]" in redact(s)


def test_redact_github_token():
    s = "token ghp_abcdefghijklmnopqrstuvwxyZ1234567890"
    assert "ghp_" not in redact(s)


def test_redact_bearer_and_assignment():
    assert "Bearer " not in redact("Authorization: Bearer abcdef1234567890")
    assert "supersecret" not in redact('api_key = "supersecret-value-123"')


def test_redact_pem_block():
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBg==\n-----END PRIVATE KEY-----"
    out = redact(pem)
    assert "MIIEvgIBADANBg" not in out
    assert "PRIVATE KEY" in out  # header label kept, material gone


def test_scan_reports_pattern_names():
    hits = scan_for_secrets("AKIAIOSFODNN7EXAMPLE plus nothing")
    assert "aws-access-key" in hits
    assert scan_for_secrets("plain hello world") == []


def test_redact_record_recursive():
    rec = {"args": {"key": "AKIAIOSFODNN7EXAMPLE", "path": "a.py"},
           "nested": [{"t": "ghp_abcdefghijklmnopqrstuvwxyZ1234567890"}]}
    out = redact_record(rec)
    assert "AKIA" not in str(out) and "ghp_" not in str(out)
    assert out["args"]["path"] == "a.py"


def test_no_false_positive_on_normal_code():
    code = "def read_file(path):\n    return open(path).read()"
    assert redact(code) == code


def test_audit_entry_contains_no_raw_secrets():
    from wisp.tools.audit import _build_entry
    entry = _build_entry(
        "run_bash", {"command": "deploy --token ghp_abcdefghijklmnopqrstuvwxyZ1234567890"},
        "/tmp", "ok", 1.0, decision="approved", forced=False, mode="full")
    assert "ghp_" not in str(entry)
    assert entry["arg_summary"]["command"].startswith("deploy ")
