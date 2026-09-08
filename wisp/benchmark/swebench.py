"""SWE-bench instance ingestion (GH#19, P2.2).

Converts official-format instance dicts into runnable BenchmarkTasks:
clone repo @ base_commit, apply the test patch, verify via FAIL_TO_PASS
(+ PASS_TO_PASS). The gold ``patch`` is NEVER written to the workspace —
it is the answer, not the setup.

Required keys: instance_id, repo, base_commit, problem_statement.
Optional: test_patch, hints_text, FAIL_TO_PASS, PASS_TO_PASS.
``repo`` accepts owner/name shorthand, full URLs, or local paths (the
last keeps this hermetically testable without network).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from wisp.benchmark.tasks import BenchmarkTask

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("instance_id", "repo", "base_commit", "problem_statement")

_CLONE_TIMEOUT_S = 120
_GIT_TIMEOUT_S = 30
_VERIFY_TIMEOUT_S = 180


def _sh(args: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    """Run one command; (rc, combined output tail). Never raises."""
    try:
        proc = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        return proc.returncode, tail[-1][:200] if tail else ""
    except subprocess.TimeoutExpired:
        return 1, f"timed out after {timeout}s: {' '.join(args[:3])}"
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def _repo_url(repo: str) -> str:
    repo = (repo or "").strip()
    if not repo:
        return ""
    if "://" in repo or repo.startswith("/") or repo.startswith("."):
        return repo
    if repo.count("/") == 1 and " " not in repo:
        return f"https://github.com/{repo}.git"
    return repo


def _setup_from_instance(inst: dict[str, Any], ws: Path) -> None:
    """Clone @ base_commit, apply test patch, drop context manifest.

    Raises on any failure — run_task records it as 'setup failed' (never
    scored as a model fault).
    """
    url = _repo_url(str(inst.get("repo", "")))
    base = str(inst.get("base_commit", "")).strip()
    if not url or not base:
        raise RuntimeError("instance missing repo/base_commit")
    rc, detail = _sh(["git", "clone", url, "."], ws, _CLONE_TIMEOUT_S)
    if rc != 0:
        raise RuntimeError(f"clone failed: {detail}")
    rc, detail = _sh(["git", "checkout", "-q", base], ws, _GIT_TIMEOUT_S)
    if rc != 0:
        raise RuntimeError(f"checkout {base[:12]} failed: {detail}")
    test_patch = str(inst.get("test_patch", "") or "")
    if test_patch.strip():
        patch_file = ws / ".wisp_test_patch.diff"
        patch_file.write_text(test_patch, encoding="utf-8")
        rc, detail = _sh(["git", "apply", str(patch_file)], ws, _GIT_TIMEOUT_S)
        patch_file.unlink(missing_ok=True)
        if rc != 0:
            raise RuntimeError(f"test_patch did not apply: {detail}")
    import json as _json

    manifest = {
        "instance_id": inst.get("instance_id", ""),
        "repo": inst.get("repo", ""),
        "base_commit": base,
        "problem_statement": inst.get("problem_statement", ""),
        "hints_text": inst.get("hints_text", ""),
    }
    (ws / "INSTANCE.json").write_text(
        _json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _verify_from_instance(inst: dict[str, Any], ws: Path) -> tuple[bool, str]:
    """Pass iff every listed FAIL_TO_PASS (+ PASS_TO_PASS) test passes."""
    fail_to_pass = [t for t in (inst.get("FAIL_TO_PASS") or []) if t]
    pass_to_pass = [t for t in (inst.get("PASS_TO_PASS") or []) if t]
    targets = fail_to_pass + pass_to_pass
    if not targets:
        return False, "no FAIL_TO_PASS/PASS_TO_PASS specified — unscorable"
    rc, detail = _sh(
        [sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"],
        ws, _VERIFY_TIMEOUT_S)
    if rc == 0:
        return True, f"{len(targets)} listed test(s) pass"
    return False, f"pytest exit {rc}: {detail}"


def patch_applies_cleanly(ws: Path, patch_text: str) -> bool:
    """Validate a captured patch via ``git apply --check -R``.

    The check runs against the dirty tree that produced the patch, so the
    REVERSE direction is the honest one: it proves the diff cleanly
    un-applies, i.e. it applied cleanly in the first place. Empty patches
    vacuously pass (nothing to apply). Never raises.
    """
    if not (patch_text or "").strip():
        return True
    import tempfile

    tmp = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".diff",
                                         delete=False) as fh:
            fh.write(patch_text if patch_text.endswith("\n") else patch_text + "\n")
            tmp = fh.name
        rc, _ = _sh(["git", "apply", "--check", "-R", tmp], ws, 30)
        return rc == 0
    except Exception:
        return False
    finally:
        try:
            if tmp:
                Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass


def task_from_swe_instance(inst: dict[str, Any]) -> BenchmarkTask:
    """Convert one SWE-bench-format instance dict to a BenchmarkTask.

    Raises ValueError on missing required keys (mirrors tasks_by_ids).
    """
    if not isinstance(inst, dict):
        raise ValueError(f"instance must be an object, got {type(inst).__name__}")
    missing = [k for k in REQUIRED_KEYS if not str(inst.get(k, "") or "").strip()]
    if missing:
        raise ValueError(f"instance missing required keys: {', '.join(missing)}")
    instance_id = str(inst["instance_id"]).strip()
    statement = str(inst["problem_statement"]).strip()
    hints = str(inst.get("hints_text", "") or "").strip()
    prompt = statement + (f"\n\nHints: {hints}" if hints else "")

    def _setup(ws: Path, _inst: dict[str, Any] = inst) -> None:
        _setup_from_instance(_inst, ws)

    def _verify(ws: Path, _inst: dict[str, Any] = inst) -> tuple[bool, str]:
        return _verify_from_instance(_inst, ws)

    return BenchmarkTask(
        id=f"swe-{instance_id}",
        title=f"[{inst.get('repo', '')}] {instance_id}",
        prompt=prompt,
        difficulty="swebench",
        instance_id=instance_id,
        setup=_setup,
        verify=_verify,
    )


def tasks_from_jsonl(path: str | Path) -> list[BenchmarkTask]:
    """Load SWE-bench-format instances JSONL (one object per line)."""
    import json as _json

    tasks: list[BenchmarkTask] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                inst = _json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}")
            try:
                tasks.append(task_from_swe_instance(inst))
            except ValueError as exc:
                raise ValueError(f"{path}:{lineno}: {exc}")
    if not tasks:
        raise ValueError(f"{path}: no instances found")
    return tasks
