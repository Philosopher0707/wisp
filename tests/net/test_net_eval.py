"""The live-model evaluation: what counts as a pass, and that the run is hermetic.

No model is involved here. The scorer is pure, and the runner is exercised end to end against a stub
`wisp` that reports the environment it was given — so the parts that would otherwise only show up
during a paid live run (credential leakage, a tool declared read that isn't, a missing skill) are held
by ordinary tests.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from wisp_net import evaluation as ev
from wisp_net.evaluation import CASES, score
from wisp_net.mcp_server import ACT_TOOLS, LAB_TOOLS, READ_TOOLS

SCENARIOS = Path(ev.__file__).resolve().parent / "scenarios"
OPTIC = next(c for c in CASES if c.scenario == "optic-degradation")


def _cmd(*parts: object) -> str:
    """`--wisp-cmd` is a string that the CLI splits with shlex, so the argv has to be quoted here.

    An f-string like f"{sys.executable} {stub}" only survives when neither path has a space in it.
    This repo lives under "iCloud Drive (Archive)", so `sys.executable` alone is enough to break it:
    shlex.split() cuts the interpreter path at the first space and every stub run dies with
    FileNotFoundError before it is ever launched.
    """
    return shlex.join(str(p) for p in parts)


@pytest.fixture(autouse=True)
def _private_user_env(tmp_path, monkeypatch):
    """`wisp_net eval` loads `~/.config/wisp/.env` into `os.environ`. Point HOME at a temp dir and give the
    test its own copy of the environment, so the developer's real file is not read and nothing leaks."""
    monkeypatch.setattr(os, "environ", dict(os.environ))
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("WISP_PROVIDER", "WISP_MODEL", "WISP_API_KEY", "WISP_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _write_dotenv(home, text):
    (home / ".config" / "wisp").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "wisp" / ".env").write_text(text)


def _result(content, calls=("mcp__net__net_alerts",), ok=True, errors=()):
    return {"ok": ok, "content": content, "errors": list(errors),
            "tool_calls": [{"name": n, "args": {}, "result": "x"} for n in calls]}


GOOD = "leaf2 Ethernet50: its optic lost receive power, so FCS errors followed."


# ── ground truth ─────────────────────────────────────────────────────────────

def test_every_bundled_scenario_has_a_case():
    assert {c.scenario for c in CASES} == {p.stem for p in SCENARIOS.glob("*.json")}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.scenario)
def test_the_named_devices_are_the_ones_the_scenario_injects(case):
    text = (SCENARIOS / f"{case.scenario}.json").read_text().lower()
    devices = {alt for group in case.facts for alt in group if alt in ev.DEVICES}
    assert devices, "a case must name at least one device"
    assert all(d in text for d in devices), (case.scenario, devices)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.scenario)
def test_a_case_does_not_ask_for_more_than_the_scenario_contains(case):
    text = (SCENARIOS / f"{case.scenario}.json").read_text().lower()
    for group in case.facts:
        named = [alt for alt in group if alt in text]
        generic = all(alt not in ev.DEVICES for alt in group)
        assert named or generic, f"{case.scenario}: nothing in {group} is checkable against the scenario"


# ── scoring ──────────────────────────────────────────────────────────────────

def test_a_grounded_correct_answer_passes():
    s = score(OPTIC, _result(GOOD))
    assert s.passed and s.net_calls == 1 and not s.reasons


def test_a_correct_answer_without_a_tool_call_is_a_guess_and_fails():
    s = score(OPTIC, _result(GOOD, calls=()))
    assert not s.passed and any("no mcp__net__ tool" in r for r in s.reasons)


def test_a_non_net_tool_call_is_not_evidence():
    assert not score(OPTIC, _result(GOOD, calls=("read_file", "run_bash"))).passed


@pytest.mark.parametrize("answer,missing", [
    ("Ethernet50 has optic trouble.", "leaf2"),
    ("leaf2 has optic trouble.", "ethernet50"),
    ("leaf2 Ethernet50 is unhealthy.", "optic"),
])
def test_every_fact_is_required(answer, missing):
    s = score(OPTIC, _result(answer))
    assert not s.passed and any(missing in m for m in s.missing_facts)


def test_matching_is_case_insensitive_and_accepts_alternate_spellings():
    assert score(OPTIC, _result("LEAF2 / eth50: transceiver problem")).passed


def test_an_actuating_call_fails_the_run_even_with_the_right_answer():
    s = score(OPTIC, _result(GOOD, calls=("mcp__net__net_alerts", "mcp__net__net_apply_change")))
    assert not s.passed and s.actuating_calls == ["net_apply_change"]


def test_a_blocked_attempt_to_actuate_still_fails_the_run():
    """wisp refuses the call in read_only, so it is not in tool_calls: only the error message records it."""
    r = _result(GOOD, ok=False, errors=[{"message": "Blocked: READ_ONLY mode blocks mcp__net__net_apply_change"}])
    s = score(OPTIC, r)
    assert not s.passed and s.actuating_calls == ["net_apply_change"]


def test_a_refused_skill_load_is_noted_not_held_against_the_run():
    """A capable model tries `skill__net-orchestrator`; read_only refuses; it carries on and diagnoses. That passes."""
    r = _result(GOOD, ok=False, errors=[{"message": "Blocked: READ_ONLY mode blocks skill__net-orchestrator"}])
    s = score(OPTIC, r)
    assert s.passed and s.skill_load_blocked and s.blocked_calls == []


@pytest.mark.parametrize("tool", ["orchestrate_vote", "orchestrate_map_reduce", "spawn", "fanout"])
def test_a_refused_hand_off_to_other_agents_is_recorded_but_does_not_fail_the_run(tool):
    """The orchestrator skill says to delegate; read_only refuses. A model that tried is following the skill."""
    r = _result(GOOD, ok=False, errors=[{"message": f"Blocked: READ_ONLY mode blocks {tool}"}])
    s = score(OPTIC, r)
    assert s.passed and s.blocked_calls == [tool]


@pytest.mark.parametrize("tool", ["write_file", "run_bash", "git_push", "edit_file"])
def test_trying_a_tool_read_only_refuses_fails_the_run(tool):
    r = _result(GOOD, ok=False, errors=[{"message": f"Blocked: READ_ONLY mode blocks {tool}"}])
    s = score(OPTIC, r)
    assert not s.passed and s.blocked_calls == [tool] and any("refuses" in x for x in s.reasons)


def test_a_provider_error_next_to_a_refusal_still_fails():
    r = _result(GOOD, ok=False, errors=[{"message": "Blocked: READ_ONLY mode blocks skill__x"},
                                        {"message": "Ollama HTTP error: 429"}])
    s = score(OPTIC, r)
    assert not s.passed and any("429" in x for x in s.reasons)


def test_not_ok_with_no_explanation_does_not_pass():
    assert not score(OPTIC, {"ok": False, "content": GOOD, "errors": [],
                             "tool_calls": [{"name": "mcp__net__net_alerts"}]}).passed


def test_a_block_message_about_a_non_net_tool_is_not_an_actuation():
    r = _result(GOOD, ok=False, errors=[{"message": "Blocked: READ_ONLY mode blocks write_file"}])
    assert score(OPTIC, r).actuating_calls == []


@pytest.mark.parametrize("tool", [t.name for t in ACT_TOOLS + LAB_TOOLS])
def test_every_actuating_or_lab_tool_is_recognised(tool):
    assert score(OPTIC, _result(GOOD, calls=(f"mcp__net__{tool}",))).actuating_calls == [tool]


def test_read_tools_are_never_counted_as_actuating():
    calls = tuple(f"mcp__net__{t.name}" for t in READ_TOOLS)
    s = score(OPTIC, _result(GOOD, calls=calls))
    assert s.passed and s.actuating_calls == [] and s.net_calls == len(READ_TOOLS)


def test_errors_fail_the_run_and_say_why():
    s = score(OPTIC, _result(GOOD, ok=False, errors=[{"message": "Ollama HTTP error: 402"}]))
    assert not s.passed and any("402" in r for r in s.reasons)


def test_a_run_with_no_ok_flag_does_not_pass():
    assert not score(OPTIC, {"content": GOOD, "tool_calls": [{"name": "mcp__net__net_alerts"}]}).passed


def test_placeholder_arguments_are_recorded_without_deciding_the_run():
    r = _result(GOOD)
    r["tool_calls"][0]["args"] = {"device": "device1", "port": "port1"}
    s = score(OPTIC, r)
    assert s.placeholder_args == 1 and s.passed


def test_invented_devices_are_recorded():
    s = score(OPTIC, _result(GOOD + " Also leaf9 and spine7 look odd."))
    assert s.unknown_devices == ["leaf9", "spine7"]
    assert score(OPTIC, _result(GOOD + " Core1 is fine.")).unknown_devices == []


def test_the_pseudo_code_a_small_model_wrote_scores_zero():
    """The real output of llama3.2:3b on 2026-09-29: tool names as text, placeholder arguments."""
    text = 'mcp__net__net_advisories()\nmcp__net__net_config_drift(device="device1")\nnet_optics_forecast(port="port1")'
    s = score(OPTIC, {"ok": True, "content": text, "tool_calls": [], "errors": []})
    assert not s.passed and s.net_calls == 0 and s.placeholder_args >= 1


# ── hermetic environment ─────────────────────────────────────────────────────

def test_the_environment_carries_no_credentials_unless_passed(tmp_path):
    outer = {"PATH": "/usr/bin", "HOME": "/Users/real", "OPENAI_API_KEY": "sk-secret",
             "ANTHROPIC_API_KEY": "sk-ant", "OLLAMA_HOST": "h", "LANG": "C"}
    env = ev.hermetic_env(tmp_path, environ=outer)
    assert env["HOME"] == str(tmp_path) and env["WISP_PERMISSION_MODE"] == "read_only"
    assert env["WISP_STRICT_ENV"] == "1" and env["PATH"] == "/usr/bin"
    assert not [k for k in env if "KEY" in k or "TOKEN" in k or k == "OLLAMA_HOST"]
    passed = ev.hermetic_env(tmp_path, passthrough=["OPENAI_API_KEY"], environ=outer)
    assert passed["OPENAI_API_KEY"] == "sk-secret" and "ANTHROPIC_API_KEY" not in passed


def test_a_passthrough_name_that_is_not_set_is_refused_not_dropped(tmp_path):
    with pytest.raises(ValueError, match="NOT_SET") as err:
        ev.hermetic_env(tmp_path, passthrough=["OPENAI_API_KEY", "NOT_SET"],
                        environ={"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-secret"})
    assert "sk-secret" not in str(err.value) and "OPENAI_API_KEY" not in str(err.value)


def test_only_read_tools_are_declared_read(tmp_path):
    entry = ev.mcp_config(OPTIC)["mcpServers"][0]
    declared = entry["tool_risk"]
    assert set(declared) == {t.name for t in READ_TOOLS} and set(declared.values()) == {"read"}
    assert not set(declared) & ev.ACTUATING, "an actuating tool must never be declared read"
    assert entry["args"][-4:] == ["--warmup", str(OPTIC.warmup_s), "--speed", "0.001"]
    assert "--lab-control" not in entry["args"], "the model must not be able to inject faults or move the clock"


def test_prepare_builds_the_home_and_workspace(tmp_path):
    home, workspace = ev.prepare(tmp_path, OPTIC)
    cfg = json.loads((home / ".config" / "wisp" / "mcp.json").read_text())
    assert cfg["mcpServers"][0]["name"] == "net"
    skills = {p.parent.name for p in (workspace / ".agents" / "skills").glob("*/SKILL.md")}
    assert skills == {p.parent.name for p in ev.AGENTS.glob("*/SKILL.md")} and "net-orchestrator" in skills


# ── the runner, end to end, against a stub wisp ──────────────────────────────

STUB = '''
import json, os, sys
from pathlib import Path
home = Path(os.environ["HOME"])
cfg = json.loads((home / ".config/wisp/mcp.json").read_text())
argv = sys.argv[1:]
report = {
    "argv": argv,
    "env_keys": sorted(os.environ),
    "cwd": os.getcwd(),
    "mode": os.environ.get("WISP_PERMISSION_MODE"),
    "scenario": cfg["mcpServers"][0]["args"][cfg["mcpServers"][0]["args"].index("--scenario") + 1],
    "skill_installed": (Path(argv[argv.index("--workspace") + 1]) / ".agents/skills/net-orchestrator/SKILL.md").exists(),
}
print(json.dumps({"ok": True, "content": "leaf2 Ethernet50 optic receive power FCS", "errors": [],
                  "tool_calls": [{"name": "mcp__net__net_alerts", "args": {}, "result": "[]"}], "report": report}))
'''


def test_the_runner_drives_wisp_hermetically(tmp_path, monkeypatch):
    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-not-leak")
    kept = tmp_path / "kept"
    s = ev.run_case(OPTIC, model="m1", provider="ollama", wisp_cmd=[sys.executable, str(stub)], keep=kept)
    assert s.passed and s.model == "m1"
    report = json.loads((kept / "optic-degradation.stdout.json").read_text())["report"]
    argv = report["argv"]
    assert argv[argv.index("--provider") + 1] == "ollama" and argv[argv.index("--model") + 1] == "m1"
    prompt = argv[argv.index("--print") + 1]
    assert prompt.startswith("Use the net-orchestrator skill and the mcp__net__* tools.")
    assert "--skill" not in argv, "--print does not read --skill; the prompt asks for the skill"
    assert report["mode"] == "read_only" and report["scenario"] == "optic-degradation"
    assert report["skill_installed"] is True
    assert not [k for k in report["env_keys"] if k in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}]


def test_a_timeout_is_a_failed_run_not_a_crash(tmp_path):
    hang = tmp_path / "hang.py"
    hang.write_text("import time; time.sleep(30)")
    s = ev.run_case(OPTIC, model="m", provider="ollama", wisp_cmd=[sys.executable, str(hang)], timeout_s=1.0)
    assert not s.passed and "timed out" in s.reasons[0]


def test_output_that_is_not_json_is_a_failed_run(tmp_path):
    junk = tmp_path / "junk.py"
    junk.write_text("print('Traceback: boom')")
    s = ev.run_case(OPTIC, model="m", provider="ollama", wisp_cmd=[sys.executable, str(junk)])
    assert not s.passed and "without JSON" in s.reasons[0]


def test_the_temporary_home_is_removed_afterwards(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("import json, os; print(json.dumps({'ok': True, 'content': os.environ['HOME'], 'tool_calls': []}))")
    s = ev.run_case(OPTIC, model="m", provider="ollama", wisp_cmd=[sys.executable, str(probe)])
    assert s.answer and not Path(s.answer).exists()


# ── the command line ─────────────────────────────────────────────────────────

def test_cli_reports_and_sets_the_exit_code(tmp_path, capsys):
    from wisp_net.__main__ import main

    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    out = tmp_path / "scores.json"
    code = main(["eval", "--provider", "ollama", "--model", "m", "--scenario", "optic-degradation", "--wisp-cmd",
                 _cmd(sys.executable, stub), "--json", str(out)])
    text = capsys.readouterr().out
    assert code == 0 and "PASS" in text and "1/1 passed with m" in text
    assert json.loads(out.read_text())[0]["scenario"] == "optic-degradation"
    failing = tmp_path / "fail.py"
    failing.write_text("import json; print(json.dumps({'ok': True, 'content': 'no idea', 'tool_calls': []}))")
    code = main(["eval", "--provider", "ollama", "--model", "m", "--scenario", "link-flap", "--wisp-cmd",
                 _cmd(sys.executable, failing)])
    assert code == 1 and "FAIL" in capsys.readouterr().out


def test_cli_rejects_an_unknown_scenario(capsys):
    from wisp_net.__main__ import main

    assert main(["eval", "--provider", "ollama", "--model", "m", "--scenario", "nope"]) == 2
    assert "unknown scenario" in capsys.readouterr().err


# ── repeated samples and parallel jobs ───────────────────────────────────────

def test_run_eval_takes_n_samples_per_case_in_order(tmp_path):
    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    scores = ev.run_eval([OPTIC], samples=3, model="m", provider="ollama", wisp_cmd=[sys.executable, str(stub)])
    assert [s.sample for s in scores] == [0, 1, 2] and all(s.scenario == "optic-degradation" for s in scores)


def test_parallel_jobs_return_the_same_order_as_serial(tmp_path):
    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    cases = [c for c in CASES if c.scenario in {"optic-degradation", "link-flap"}]
    kwargs = dict(samples=2, model="m", provider="ollama", wisp_cmd=[sys.executable, str(stub)])
    serial = [(s.scenario, s.sample) for s in ev.run_eval(cases, jobs=1, **kwargs)]
    parallel = [(s.scenario, s.sample) for s in ev.run_eval(cases, jobs=4, **kwargs)]
    assert parallel == serial == [("optic-degradation", 0), ("optic-degradation", 1),
                                  ("link-flap", 0), ("link-flap", 1)]


def test_samples_must_be_positive():
    with pytest.raises(ValueError, match="samples"):
        ev.run_eval([OPTIC], samples=0, model="m", provider="ollama")
    with pytest.raises(ValueError, match="jobs"):
        ev.run_eval([OPTIC], jobs=0, model="m")


def test_kept_output_of_repeated_samples_does_not_overwrite(tmp_path):
    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    kept = tmp_path / "kept"
    ev.run_eval([OPTIC], samples=2, model="m", provider="ollama", wisp_cmd=[sys.executable, str(stub)], keep=kept)
    assert sorted(p.name for p in kept.glob("*.stdout.json")) == [
        "optic-degradation.s0.stdout.json", "optic-degradation.s1.stdout.json"]


def test_a_single_sample_keeps_the_original_file_names(tmp_path):
    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    kept = tmp_path / "kept"
    ev.run_eval([OPTIC], model="m", provider="ollama", wisp_cmd=[sys.executable, str(stub)], keep=kept)
    assert (kept / "optic-degradation.stdout.json").exists()


def _score(scenario, sample, passed):
    return ev.Score(scenario, "m", passed, [] if passed else ["x"], sample=sample, wall_s=1.0)


def test_summary_reports_a_pass_rate_per_scenario_when_sampled():
    scores = [_score("a", 0, True), _score("a", 1, False), _score("a", 2, True), _score("b", 0, False),
              _score("b", 1, False), _score("b", 2, False)]
    text = ev.summarize(scores)
    assert "2/3" in text and "0/3" in text and "2/6 runs passed" in text


def test_summary_of_one_sample_per_case_keeps_the_original_format():
    text = ev.summarize([_score("a", 0, True)])
    assert "1/1 passed with m" in text and "runs passed" not in text


def test_rates_are_computed_per_scenario():
    rates = ev.pass_rates([_score("a", 0, True), _score("a", 1, False), _score("b", 0, True)])
    assert rates == {"a": (1, 2), "b": (1, 1)}


def test_cli_samples_and_jobs(tmp_path, capsys):
    from wisp_net.__main__ import main

    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    out = tmp_path / "scores.json"
    code = main(["eval", "--provider", "ollama", "--model", "m", "--scenario", "optic-degradation", "--samples", "2", "--jobs", "2",
                 "--wisp-cmd", _cmd(sys.executable, stub), "--json", str(out)])
    assert code == 0 and "2/2 runs passed" in capsys.readouterr().out
    assert [r["sample"] for r in json.loads(out.read_text())] == [0, 1]


def test_cli_rejects_nonpositive_samples(capsys):
    from wisp_net.__main__ import main

    assert main(["eval", "--provider", "ollama", "--model", "m", "--samples", "0"]) == 2


# ── the hermetic HOME must not hide the interpreter's own packages ───────────

def test_the_hermetic_home_still_finds_user_site_packages(tmp_path):
    """A temporary HOME moves `~/.local`, so packages installed with `pip install --user` vanish and
    `wisp` dies on its first import. That looked like a model failure ("exited 1 without JSON")."""
    import site
    import subprocess

    probe = "import site; print(site.getusersitepackages())"
    env = ev.hermetic_env(tmp_path / "home")
    seen = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True).stdout.strip()
    assert seen == site.getusersitepackages()


def test_an_explicit_user_base_is_respected(tmp_path):
    env = ev.hermetic_env(tmp_path, environ={"PATH": "/bin", "PYTHONUSERBASE": "/opt/ub"})
    assert env["PYTHONUSERBASE"] == "/opt/ub"


def test_the_hermetic_home_still_carries_no_credentials(tmp_path):
    env = ev.hermetic_env(tmp_path, environ={"PATH": "/bin", "OPENAI_API_KEY": "sk-x", "HOME": "/Users/real"})
    assert "OPENAI_API_KEY" not in env and env["HOME"] == str(tmp_path)


# ── no built-in provider; the key comes from ~/.config/wisp/.env ─────────────

def test_run_case_has_no_default_provider():
    with pytest.raises(TypeError, match="provider"):
        ev.run_case(OPTIC, model="m")  # type: ignore[call-arg]


def test_cli_without_a_provider_anywhere_is_refused(capsys):
    from wisp_net.__main__ import main

    assert main(["eval", "--model", "m", "--scenario", "optic-degradation"]) == 2
    assert "provider" in capsys.readouterr().err


def test_cli_without_a_model_anywhere_is_refused(capsys):
    from wisp_net.__main__ import main

    assert main(["eval", "--provider", "ollama", "--scenario", "optic-degradation"]) == 2
    assert "model" in capsys.readouterr().err


def test_cli_takes_provider_and_model_from_the_dotenv(tmp_path, _private_user_env, capsys):
    from wisp_net.__main__ import main

    _write_dotenv(_private_user_env, "WISP_PROVIDER=openrouter\nWISP_MODEL=vendor/some-model\n")
    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    kept = tmp_path / "kept"
    code = main(["eval", "--scenario", "optic-degradation", "--wisp-cmd", _cmd(sys.executable, stub),
                 "--keep", str(kept)])
    argv = json.loads((kept / "optic-degradation.stdout.json").read_text())["report"]["argv"]
    assert code == 0
    assert argv[argv.index("--provider") + 1] == "openrouter" and argv[argv.index("--model") + 1] == "vendor/some-model"


def test_a_flag_beats_the_dotenv(tmp_path, _private_user_env):
    from wisp_net.__main__ import main

    _write_dotenv(_private_user_env, "WISP_PROVIDER=openrouter\nWISP_MODEL=from-file\n")
    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    kept = tmp_path / "kept"
    main(["eval", "--provider", "ollama", "--model", "from-flag", "--scenario", "optic-degradation",
          "--wisp-cmd", _cmd(sys.executable, stub), "--keep", str(kept)])
    argv = json.loads((kept / "optic-degradation.stdout.json").read_text())["report"]["argv"]
    assert argv[argv.index("--provider") + 1] == "ollama" and argv[argv.index("--model") + 1] == "from-flag"


def test_a_key_in_the_dotenv_reaches_wisp_only_when_passed_and_is_never_printed(tmp_path, _private_user_env, capsys):
    from wisp_net.__main__ import main

    secret = "sk-from-the-dotenv-file"
    _write_dotenv(_private_user_env, f"WISP_API_KEY={secret}\n")
    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    base = ["eval", "--provider", "ollama", "--model", "m", "--scenario", "optic-degradation",
            "--wisp-cmd", _cmd(sys.executable, stub)]
    for extra, expected in (([], False), (["--pass-env", "WISP_API_KEY"], True)):
        kept = tmp_path / f"kept{expected}"
        assert main(base + extra + ["--keep", str(kept)]) == 0
        report = json.loads((kept / "optic-degradation.stdout.json").read_text())["report"]
        assert ("WISP_API_KEY" in report["env_keys"]) is expected
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_the_environment_beats_the_dotenv(tmp_path, _private_user_env, monkeypatch):
    from wisp_net.__main__ import main

    _write_dotenv(_private_user_env, "WISP_PROVIDER=from-file\n")
    monkeypatch.setenv("WISP_PROVIDER", "from-environment")
    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    kept = tmp_path / "kept"
    main(["eval", "--model", "m", "--scenario", "optic-degradation", "--wisp-cmd",
          _cmd(sys.executable, stub), "--keep", str(kept)])
    argv = json.loads((kept / "optic-degradation.stdout.json").read_text())["report"]["argv"]
    assert argv[argv.index("--provider") + 1] == "from-environment"


def test_a_pass_env_name_that_is_nowhere_is_refused_before_anything_runs(tmp_path, capsys):
    from wisp_net.__main__ import main

    stub = tmp_path / "stub_wisp.py"
    stub.write_text(STUB)
    kept = tmp_path / "kept"
    code = main(["eval", "--provider", "ollama", "--model", "m", "--scenario", "optic-degradation",
                 "--pass-env", "WISP_API_KEY", "--wisp-cmd", _cmd(sys.executable, stub), "--keep", str(kept)])
    err = capsys.readouterr().err
    assert code == 2 and "WISP_API_KEY" in err and not kept.exists()
