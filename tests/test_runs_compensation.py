# tests/test_runs_compensation.py — pure compensation + repro manifest (M3).
from wisp.runs.compensation import EditRecord, reversibility, rollback_preview
from wisp.runs.repro import ReproManifest


def test_rollback_preview_restores_preimage():
    rec = EditRecord(path="a.py", unified_diff="--- a\na\n+++ b\nb\n",
                     pre_image_hash="h1", reversible=True)
    prev = rollback_preview(rec)
    assert "a.py" in prev and "h1" in prev
    assert "git checkout" in prev or "revert" in prev.lower()


def test_irreversible_flags_warning():
    rec = EditRecord(path="db.sqlite", unified_diff="", pre_image_hash="",
                     reversible=False, note="external side effect")
    prev = rollback_preview(rec)
    assert "cannot" in prev.lower() or "manual" in prev.lower()


def test_reversibility_table():
    assert reversibility("read_file") == "reversible"
    assert reversibility("write_file") == "reversible"
    assert reversibility("run_bash") == "unknown"
    assert reversibility("git_push") == "irreversible"


def test_repro_manifest_hash_stable():
    m = ReproManifest(wisp_version="1.2.3", model="ollama/qwen",
                      provider="ollama", workspace_commit="abc123",
                      input_hash="in1", output_hash="out1")
    assert m.manifest_hash() == m.manifest_hash()
    assert ReproManifest.from_dict(m.to_dict()) == m
    assert len(m.manifest_hash()) == 32
