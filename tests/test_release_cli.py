# tests/test_release_cli.py — release CLI goldens (M7 T2).
import io
import json

from wisp.release.cli import main as release_main


def _run(args, monkeypatch=None, tmp_path=None):
    if tmp_path is not None and monkeypatch is not None:
        monkeypatch.setenv("WISP_DB", str(tmp_path / "h.db"))
        monkeypatch.setenv("WISP_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    out = io.StringIO()
    code = release_main(args, out=out)
    return code, out.getvalue()


def test_health(tmp_path, monkeypatch):
    code, text = _run(["health"], monkeypatch, tmp_path)
    assert code == 0 and "[ok] python-version" in text


def test_lock_writes_file(tmp_path, monkeypatch):
    dest = tmp_path / "requirements.lock"
    code, text = _run(["lock", "--out", str(dest)], monkeypatch, tmp_path)
    assert code == 0
    content = dest.read_text()
    assert "requests==" in content and "cryptography==" in content


def test_sbom_stdout():
    code, text = _run(["sbom"])
    assert code == 0
    sbom = json.loads(text)
    assert sbom["bomFormat"] == "CycloneDX"


def test_diagnostics_redacted(tmp_path, monkeypatch):
    dest = tmp_path / "diag.json"
    code, _ = _run(["diagnostics", "--out", str(dest)], monkeypatch, tmp_path)
    assert code == 0
    bundle = json.loads(dest.read_text())
    assert bundle["redacted"] is True and "health" in bundle


def test_unknown_command():
    out = io.StringIO()
    assert release_main(["frobnicate"], out=out) == 2
