"""G1A journal corruption battery + fuzz + idempotence (§17/§18/§10).

Every case: recover() must not crash, must not report clean/completed/
rolled-back, must preserve the corrupt bytes as .corrupt-* evidence, and
must leave the workspace unmutated.
"""
from __future__ import annotations

import json
import os
import random

from wisp.workspace import _journal_dir, recover

J = "cs-test01.json"


def _ws_jdir(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)
    jd = _journal_dir(ws)
    os.makedirs(jd, exist_ok=True)
    return ws, jd


def _write(jd, name, raw: bytes):
    with open(os.path.join(jd, name), "wb") as fh:
        fh.write(raw)


def _valid_plan():
    return {"changeset": "cs-test01", "base": "b1",
            "plan": [{"op": "CREATE", "path": "a.txt", "rename_from": "",
                      "content": "hi", "artifact": ""}],
            "done": [], "pre": {"a.txt": None}, "post": {},
            "completed": False}


def _check_blocked(ws, jd, raw_name=J):
    before = {f for f in os.listdir(ws) if not f.startswith(".")}
    result = recover(ws)
    assert result.startswith("blocked:"), result
    assert "rolled-back" not in result and result != "clean", result
    assert not os.path.exists(os.path.join(jd, raw_name)), "corrupt file kept live"
    quar = [f for f in os.listdir(jd) if ".corrupt-" in f]
    assert len(quar) == 1, os.listdir(jd)
    assert os.path.exists(os.path.join(jd, quar[0])), "evidence preserved"
    after = {f for f in os.listdir(ws) if not f.startswith(".")}
    assert before == after, "workspace mutated during blocked recovery"
    return result, quar[0]


def test_1_empty_journal(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    _write(jd, J, b"")
    _check_blocked(ws, jd)


def test_2_truncated_json(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    full = json.dumps(_valid_plan()).encode()
    _write(jd, J, full[: len(full) // 2])
    _check_blocked(ws, jd)


def test_3_invalid_json(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    _write(jd, J, b"{not json,,,")
    _check_blocked(ws, jd)


def test_4_missing_required_fields(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    for drop in ("plan", "changeset", "done", "completed", "base"):
        p = _valid_plan()
        del p[drop]
        _write(jd, J, json.dumps(p).encode())
        _check_blocked(ws, jd)
        os.unlink(os.path.join(jd, [f for f in os.listdir(jd) if ".corrupt-" in f][0]))


def test_4b_missing_pre_completes_rollback_refuses(tmp_path):
    # pre-images are rollback evidence, not forward-commit input: a pre-less
    # journal converges forward, but rollback must refuse without pre-images.
    from wisp.workspace import (snapshot_workspace, make_changeset,
                                rollback_changeset, Change)
    ws, jd = _ws_jdir(tmp_path)
    base0 = snapshot_workspace(ws)
    cs = make_changeset(base0.id, {"run_id": "r", "node_id": "n"},
                        [Change(path="a.txt", op="CREATE", content="hi")])
    p = {"changeset": cs.id, "base": base0.id,
         "plan": [{"op": "CREATE", "path": "a.txt", "rename_from": "",
                   "content": "hi", "artifact": ""}],
         "done": [], "post": {}, "completed": False}  # no "pre"
    with open(os.path.join(jd, f"{cs.id}.json"), "w") as fh:
        json.dump(p, fh)
    assert recover(ws) == "completed:1"
    base = snapshot_workspace(ws)
    r = rollback_changeset(ws, base, cs)
    assert not r.ok, r


def test_5_invalid_field_types(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    bads = [
        {**_valid_plan(), "plan": "nope"},
        {**_valid_plan(), "done": "nope"},
        {**_valid_plan(), "completed": "yes"},
        {**_valid_plan(), "pre": []},
        {**_valid_plan(), "plan": [{"op": "CREATE"}]},
        {**_valid_plan(), "plan": [{"op": "NUKXE", "path": "a.txt"}]},
    ]
    for p in bads:
        _write(jd, J, json.dumps(p).encode())
        _check_blocked(ws, jd)
        os.unlink(os.path.join(jd, [f for f in os.listdir(jd) if ".corrupt-" in f][0]))


def test_6_unexpected_schema_version(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    p = _valid_plan()
    p["schema_version"] = 999
    p["plan"] = [{"op": "CREATE", "path": "a.txt", "rename_from": "",
                  "content": "hi", "artifact": "", "future_field": [1, 2]}]
    _write(jd, J, json.dumps(p, default=str).encode())
    # unknown extra fields are tolerated (forward-compatible); shape decides
    result = recover(ws)
    assert result == "completed:1", result
    assert (tmp_path / "ws" / "a.txt").read_text() == "hi"


def test_6b_contentless_item_blocked_no_crash(tmp_path):
    # plan item without content must fail closed, never crash recover().
    ws, jd = _ws_jdir(tmp_path)
    p = _valid_plan()
    p["plan"] = [{"op": "CREATE", "path": "a.txt"}]
    _write(jd, J, json.dumps(p).encode())
    _check_blocked(ws, jd)
    assert not (tmp_path / "ws" / "a.txt").exists()


def test_7_completed_with_drift_is_skipped_safely(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    p = _valid_plan()
    p["completed"] = True
    p["done"] = ["a.txt"]
    _write(jd, J, json.dumps(p).encode())
    with open(os.path.join(ws, "a.txt"), "w") as fh:
        fh.write("drifted")
    assert recover(ws) == "clean"  # nothing pending: no work, no lie
    assert open(os.path.join(ws, "a.txt")).read() == "drifted"


def test_8_journal_pointing_outside_workspace(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    for evil in ("../evil.txt", "/abs.txt", "a/../../b.txt"):
        p = _valid_plan()
        p["plan"] = [{"op": "CREATE", "path": evil, "rename_from": "",
                      "content": "x", "artifact": ""}]
        _write(jd, J, json.dumps(p).encode())
        _check_blocked(ws, jd)
        os.unlink(os.path.join(jd, [f for f in os.listdir(jd) if ".corrupt-" in f][0]))
    assert not os.path.exists("/tmp/g1a-evil-probe")


def test_9_malformed_paths(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    for evil in ("a\x00b.txt", "a\x01b.txt", "", "x" * 600):
        p = _valid_plan()
        p["plan"] = [{"op": "CREATE", "path": evil, "rename_from": "",
                      "content": "x", "artifact": ""}]
        raw = json.dumps(p).encode("utf-8", "replace")
        _write(jd, J, raw)
        _check_blocked(ws, jd)
        os.unlink(os.path.join(jd, [f for f in os.listdir(jd) if ".corrupt-" in f][0]))


def test_recovery_idempotent_on_blocked(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    _write(jd, J, b"{torn")
    r1 = recover(ws)
    files1 = sorted(os.listdir(jd))
    r2 = recover(ws)
    r3 = recover(ws)
    # second/third runs see only quarantined evidence: clean, no new files
    assert r1.startswith("blocked:"), r1
    assert r2 == "clean" and r3 == "clean", (r2, r3)
    assert sorted(os.listdir(jd)) == files1, "recovery must not duplicate evidence"


def test_stale_temp_swept_valid_completes(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    _write(jd, "cs-x.json.tmp-123-456", b"partial-temp-bytes")
    _write(jd, J, json.dumps(_valid_plan()).encode())
    assert recover(ws) == "completed:1"
    assert not [f for f in os.listdir(jd) if ".tmp-" in f], "temp swept"


def test_quarantine_name_is_safe(tmp_path):
    ws, jd = _ws_jdir(tmp_path)
    evil_name = "../../..\\evil\tevil.json"
    _write(jd, J, b"{torn")
    # quarantine derives from the on-disk name (safe), not journal content
    result, qname = _check_blocked(ws, jd)
    assert "/" not in qname and "\\" not in qname and len(qname) < 128
    _ = evil_name  # traversal payloads live in plan paths (test_8), not names


def test_fuzz_journals_never_false_success(tmp_path):
    rng = random.Random(13001)
    alphabet = list("abcdefghijklmnopqrstuvwxyz_{}\":[]0123456789\x00\xff\u00e9../")
    base = json.dumps(_valid_plan()).encode()
    n_blocked = 0
    for i in range(300):
        ws = str(tmp_path / f"fz{i}")
        os.makedirs(ws, exist_ok=True)
        jd = _journal_dir(ws)
        os.makedirs(jd, exist_ok=True)
        mode = rng.randrange(5)
        if mode == 0:  # random truncation
            raw = base[: rng.randrange(len(base) + 1)]
        elif mode == 1:  # random byte corruption
            raw = bytearray(base)
            for _ in range(rng.randrange(1, 6)):
                raw[rng.randrange(len(raw))] = rng.randrange(256)
            raw = bytes(raw)
        elif mode == 2:  # random alphabet soup
            raw = "".join(rng.choice(alphabet) for _ in range(rng.randrange(400))).encode(
                "utf-8", "replace")
        elif mode == 3:  # duplicated top-level key (last wins in json)
            raw = base[:-1] + b', "completed": "maybe"}'
        else:  # oversized field
            p = _valid_plan()
            p["plan"] = [{"op": "CREATE", "path": "a.txt", "rename_from": "",
                          "content": "z" * rng.randrange(2000000), "artifact": ""}]
            raw = json.dumps(p).encode()
        with open(os.path.join(jd, J), "wb") as fh:
            fh.write(raw)
        result = recover(ws)
        assert "rolled-back" not in result, (i, mode, result)
        if result.startswith("blocked:"):
            n_blocked += 1
            continue
        # non-blocked outcomes are only legal on intact-enough journals
        assert result in ("clean", "completed:1"), (i, mode, result)
        if result == "completed:1":
            # forward completion must have converged truthfully
            assert (tmp_path / f"fz{i}" / "a.txt").exists()
    assert n_blocked > 100, "fuzz must actually exercise the blocked path"
