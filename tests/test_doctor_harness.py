"""`wisp doctor`: each harness check passes on the real code and FAILS when its invariant is broken.

A check that cannot fail is decoration, so every check has a mutation test: the invariant is broken in the
code under test (not in the check) and the check must go red.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from wisp.cli import doctor_harness as dh
from wisp.core.doctor import CheckStatus


@pytest.fixture(autouse=True)
def hermetic(tmp_path, monkeypatch):
    """No test reads the developer's home, skills, settings or live audit log."""
    home = tmp_path / "home"
    (home / ".agents" / "skills").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WISP_AUDIT_LOG", str(tmp_path / "live-audit.jsonl"))
    monkeypatch.delenv("WISP_TOOL_PROFILE", raising=False)
    monkeypatch.setattr("wisp.skills.GLOBAL_SKILL_DIRS", [home / ".agents" / "skills"])
    # A one-repo fleet that is clean, so no test reads the developer's real manifest or repos.
    monkeypatch.setenv("WISP_FLEET_MANIFEST", str(_fleet(tmp_path / "fleet", ["main-repo"])))
    return home


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def _fleet(root: Path, names: list[str], *, scan: Path | None = None) -> Path:
    """A manifest over real, clean git repos; the first is the orchestrator. Returns the manifest path."""
    root.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, n in enumerate(names):
        repo = root / n
        repo.mkdir()
        _git(repo, "init", "-q")
        (repo / "f").write_text("x")
        _git(repo, "add", "f")
        _git(repo, "commit", "-q", "-m", "init")
        role = "orchestrator" if i == 0 else "tool"
        lines.append(f'[[repo]]\nname = "{n}"\npath = "{repo}"\nrole = "{role}"\nremote_required = false\n')
    if scan is not None:
        lines.append(f'[fleet]\nscan = ["{scan}"]\n')
    manifest = root / "wisp.fleet.toml"
    manifest.write_text("\n".join(lines))
    return manifest


def _run(check) -> tuple[CheckStatus, str]:
    result = check()
    return result.status, result.message


def test_names_are_pinned():
    assert dh.HARNESS_CHECK_NAMES == (
        "tool_profile", "subagent_surface", "spend_accounting", "context_budget",
        "global_skills", "skill_capture", "audit_chain", "repl_input", "fleet",
    )


def test_every_check_passes_on_the_real_code():
    report = dh.run_harness_checks()
    assert [c.name for c in report.checks] == list(dh.HARNESS_CHECK_NAMES)
    assert [(c.name, c.message) for c in report.checks if c.status is not CheckStatus.OK] == []


# ── tool_profile ─────────────────────────────────────────────────────────────────────────────────────────────


def test_tool_profile_full_is_a_warning(monkeypatch):
    monkeypatch.setenv("WISP_TOOL_PROFILE", "full")
    status, message = _run(dh._check_tool_profile)
    assert status is CheckStatus.WARN and "full" in message


def test_tool_profile_fails_when_a_core_tool_has_no_implementation(monkeypatch):
    from wisp.tools.registry import TOOL_IMPLS

    monkeypatch.delitem(TOOL_IMPLS, "grep")
    status, message = _run(dh._check_tool_profile)
    assert status is CheckStatus.FAIL and "grep" in message


# ── subagent_surface ─────────────────────────────────────────────────────────────────────────────────────────


def test_subagent_surface_fails_when_skill_tools_reach_children(monkeypatch):
    monkeypatch.setattr("wisp.core.stateless._SUBAGENT_EXCLUDED_TOOL_PREFIXES", ())
    assert _run(dh._check_subagent_surface)[0] is CheckStatus.FAIL


# ── spend_accounting ─────────────────────────────────────────────────────────────────────────────────────────


def test_spend_accounting_fails_when_each_delta_is_floored(monkeypatch):
    from wisp.multi_agent.resource_budget import ResourceBudget

    def floored(self, text: str) -> None:
        self._tokens_used += len(text) // 4  # the old bug: a stream of short deltas costs nothing

    monkeypatch.setattr(ResourceBudget, "record_text", floored)
    status, message = _run(dh._check_spend_accounting)
    assert status is CheckStatus.FAIL and "counted 0" in message


def test_spend_accounting_fails_when_streamed_text_is_not_metered(monkeypatch):
    from wisp.multi_agent.resource_budget import ResourceBudget

    monkeypatch.delattr(ResourceBudget, "record_text")
    assert _run(dh._check_spend_accounting)[0] is CheckStatus.FAIL


def test_spend_accounting_warns_when_the_global_ceiling_is_off(monkeypatch):
    monkeypatch.setenv("WISP_SUBAGENT_TOKEN_BUDGET", "0")
    assert _run(dh._check_spend_accounting)[0] is CheckStatus.WARN


# ── context_budget ───────────────────────────────────────────────────────────────────────────────────────────


def test_context_budget_fails_when_the_fit_overshoots(monkeypatch):
    monkeypatch.setattr("wisp.context_assembler.ContextAssembler._fit_sections",
                        lambda self, sections, max_tokens: ("x" * 9000, 3000))
    assert _run(dh._check_context_budget)[0] is CheckStatus.FAIL


def test_context_budget_fails_when_a_cut_is_not_named(monkeypatch):
    monkeypatch.setattr("wisp.context_assembler.ContextAssembler._fit_sections",
                        lambda self, sections, max_tokens: ("r" * 300, 100))  # fits, but silently dropped the rest
    status, message = _run(dh._check_context_budget)
    assert status is CheckStatus.FAIL and "named" in message


# ── global_skills ────────────────────────────────────────────────────────────────────────────────────────────


def test_global_skills_fails_when_another_tools_skill_dir_is_loaded(monkeypatch, hermetic):
    monkeypatch.setattr("wisp.skills.GLOBAL_SKILL_DIRS",
                        [hermetic / ".agents" / "skills", hermetic / ".claude" / "skills"])
    assert _run(dh._check_global_skills)[0] is CheckStatus.FAIL


def test_global_skills_fails_when_captured_skills_are_not_loaded_back(monkeypatch):
    from wisp import skills

    monkeypatch.setattr(skills, "SKILL_DIR_NAMES", [d for d in skills.SKILL_DIR_NAMES if "auto" not in d])
    assert _run(dh._check_global_skills)[0] is CheckStatus.FAIL


def test_global_skills_warns_when_the_menu_is_large(hermetic):
    for i in range(dh._GLOBAL_SKILL_WARN_COUNT + 1):
        d = hermetic / ".agents" / "skills" / f"s{i}"
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: s{i}\ndescription: skill number {i}\n---\nbody\n")
    status, message = _run(dh._check_global_skills)
    assert status is CheckStatus.WARN and str(dh._GLOBAL_SKILL_WARN_COUNT + 1) in message


# ── skill_capture ────────────────────────────────────────────────────────────────────────────────────────────


def test_skill_capture_fails_when_nothing_is_written(monkeypatch):
    monkeypatch.setattr("wisp.skill_capture.capture_resolved_skill", lambda *a, **k: None)
    assert _run(dh._check_skill_capture)[0] is CheckStatus.FAIL


def test_skill_capture_fails_when_discovery_cannot_find_the_skill(monkeypatch):
    from wisp import skills

    monkeypatch.setattr(skills, "SKILL_DIR_NAMES", [d for d in skills.SKILL_DIR_NAMES if "auto" not in d])
    status, message = _run(dh._check_skill_capture)
    assert status is CheckStatus.FAIL and "load" in message


# ── audit_chain ──────────────────────────────────────────────────────────────────────────────────────────────


def test_audit_chain_fails_when_writers_chain_from_memory(monkeypatch):
    from wisp.infra.audit import AuditTrail

    monkeypatch.setattr(AuditTrail, "_read_head", staticmethod(lambda fh: ""))  # the old fork
    status, message = _run(dh._check_audit_chain)
    assert status is CheckStatus.FAIL and "forked" in message


def test_audit_chain_warns_on_a_broken_live_log_and_never_touches_it(tmp_path):
    live = tmp_path / "live-audit.jsonl"
    live.write_text('{"_prev_hash":"","_hash":"a"}\n{"_prev_hash":"WRONG","_hash":"b"}\n')
    before = live.read_bytes()
    status, message = _run(dh._check_audit_chain)
    assert status is CheckStatus.WARN and "historical break" in message
    assert live.read_bytes() == before, "the live audit log is evidence; the doctor must not rewrite it"


# ── repl_input ───────────────────────────────────────────────────────────────────────────────────────────────


def test_repl_input_fails_when_a_shortcut_swallows_the_first_letter(monkeypatch):
    from prompt_toolkit.key_binding import KeyBindings

    def swallowing(model):  # the old behaviour: `v` and space are eaten on an empty prompt
        kb = KeyBindings()
        kb.add("v")(lambda event: None)
        return kb

    monkeypatch.setattr("wisp.cli.repl.build_key_bindings", swallowing)
    status, message = _run(dh._check_repl_input)
    assert status is CheckStatus.FAIL and "swallowed" in message


def test_repl_input_fails_when_readline_still_owns_prompt_toolkits_history(monkeypatch):
    monkeypatch.setattr("wisp.cli.repl.own_history", lambda path: None)  # ownership never recorded
    status, message = _run(dh._check_repl_input)
    assert status is CheckStatus.FAIL and "history" in message


# ── fleet ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_fleet_warns_when_there_is_no_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("WISP_FLEET_MANIFEST", str(tmp_path / "absent.toml"))
    assert _run(dh._check_fleet)[0] is CheckStatus.WARN


def test_fleet_fails_on_an_untrustworthy_manifest(monkeypatch, tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not toml")
    monkeypatch.setenv("WISP_FLEET_MANIFEST", str(bad))
    assert _run(dh._check_fleet)[0] is CheckStatus.FAIL


def test_fleet_fails_when_a_declared_repo_is_gone(monkeypatch, tmp_path):
    import shutil

    manifest = _fleet(tmp_path / "gone", ["a", "b"])
    shutil.rmtree(tmp_path / "gone" / "b")
    monkeypatch.setenv("WISP_FLEET_MANIFEST", str(manifest))
    status, message = _run(dh._check_fleet)
    assert status is CheckStatus.FAIL and "b" in message


def test_fleet_warns_on_a_dirty_repo(monkeypatch, tmp_path):
    manifest = _fleet(tmp_path / "dirty", ["a"])
    (tmp_path / "dirty" / "a" / "f").write_text("changed")
    monkeypatch.setenv("WISP_FLEET_MANIFEST", str(manifest))
    status, message = _run(dh._check_fleet)
    assert status is CheckStatus.WARN and "1 dirty" in message


def test_fleet_warns_on_an_unmanaged_repo(monkeypatch, tmp_path):
    scan = tmp_path / "scan"
    (scan / "stray").mkdir(parents=True)
    _git(scan / "stray", "init", "-q")
    monkeypatch.setenv("WISP_FLEET_MANIFEST", str(_fleet(tmp_path / "um", ["a"], scan=scan)))
    status, message = _run(dh._check_fleet)
    assert status is CheckStatus.WARN and "unmanaged" in message


def test_fleet_warns_when_the_mcp_config_is_missing_a_worker(monkeypatch, tmp_path, hermetic):
    manifest = _fleet(tmp_path / "wk", ["a", "w"])
    manifest.write_text(manifest.read_text() + '\n[repo.worker]\ncommand = ["/bin/echo", "hi"]\n')  # `w` is last
    monkeypatch.setenv("WISP_FLEET_MANIFEST", str(manifest))
    status, message = _run(dh._check_fleet)
    assert status is CheckStatus.WARN and "fleet workers --write" in message

    from wisp.fleet import load_manifest, merge_mcp_config, render_mcp_servers

    target = hermetic / ".config" / "wisp" / "mcp.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(merge_mcp_config(None, render_mcp_servers(load_manifest(manifest)))))
    assert _run(dh._check_fleet)[0] is CheckStatus.OK, "an in-sync MCP config is clean"


# ── runner and CLI ───────────────────────────────────────────────────────────────────────────────────────────


def test_a_crashing_probe_is_a_failure_and_the_rest_still_run(monkeypatch):
    def boom() -> None:
        raise RuntimeError("probe bug")

    monkeypatch.setattr(dh, "_CHECKS", (("tool_profile", boom), *dh._CHECKS[1:]))
    report = dh.run_harness_checks()
    assert report.checks[0].status is CheckStatus.FAIL and "probe bug" in report.checks[0].message
    assert len(report.checks) == len(dh.HARNESS_CHECK_NAMES)


def test_main_json_and_exit_codes(monkeypatch, capsys):
    assert dh.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == len(dh.HARNESS_CHECK_NAMES)

    monkeypatch.setattr("wisp.core.stateless._SUBAGENT_EXCLUDED_TOOL_PREFIXES", ())
    assert dh.main([]) == 1


def test_wisp_doctor_is_a_registered_subcommand():
    from wisp.__main__ import _SUBCOMMAND_NAMES, print_subcommand_help

    assert "doctor" in _SUBCOMMAND_NAMES and print_subcommand_help("doctor")


def test_slash_doctor_deep_runs_the_harness_checks(capsys):
    from wisp.repl.commands.doctor import cmd_doctor

    cmd_doctor(None, "--deep --json")
    assert json.loads(capsys.readouterr().out)["total"] == len(dh.HARNESS_CHECK_NAMES)
