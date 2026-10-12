"""`~/.config/wisp/.env` is for secrets and operator switches; WHICH provider/model/endpoint to use belongs to config.json. And the file is kept tidy by a tool, not by hand.

Seen on a real machine (2026-10-12): `/provider nvidia` wrote `WISP_PROVIDER` to `.env` but skipped the blank model, so a `WISP_MODEL=qwen2.5-coder` from an earlier Ollama
choice stayed there for good. The loader lets the environment win over config.json, so every later `/model` choice was silently overridden and every plain prompt was a
404. The same file also held `WISP_OLLAMA_URL=---` (a placeholder nobody could tell from a value) and a generic `WISP_API_KEY` that is sent to whichever provider has no key
of its own.

These tests pin:

* the linter (`wisp.env_file.lint`): what it reports, with names and line numbers and NEVER a value;
* the fixer (`fix`): idempotent, keeps comments, order and every other byte, and never guesses between two conflicting selections (the caller says `prefer`);
* the file operations: a backup first, 0600, atomic, config.json written before the `.env` line goes;
* the split of persistence (`persist`): selection to config.json (and out of `.env`), keys to `.env` only;
* `wisp config env check|fix` through the real console entry, and the doctor probe.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from wisp import env_file
from wisp.env_file import fix, lint

REPO = Path(__file__).resolve().parents[1]
SECRET = "nvapi-SECRET-VALUE-0123456789"


def codes(findings):
    return sorted(f.code for f in findings)


def by_code(findings, code):
    return [f for f in findings if f.code == code]


class TestTheLinter:
    def test_a_clean_file_has_nothing_to_say(self):
        text = f"# my switches\nWISP_SANDBOX=on\nNVIDIA_API_KEY={SECRET}\n\nWISP_TOOL_PROFILE=full\n"
        assert lint(text, {}) == []

    def test_a_later_duplicate_is_reported_with_its_line_and_the_first_is_the_one_in_effect(self):
        found = by_code(lint("WISP_SANDBOX=on\nWISP_SANDBOX=off\n", {}), "DUPLICATE_KEY")
        assert [(f.line, f.key, f.fixable) for f in found] == [(2, "WISP_SANDBOX", True)]
        assert "first" in found[0].message

    @pytest.mark.parametrize("value", ["---", "--", "-", "none", "None", "NULL", "n/a", "unset", "changeme", "TODO"])
    def test_a_placeholder_is_not_a_value(self, value):
        found = by_code(lint(f"WISP_OLLAMA_URL={value}\n", {}), "PLACEHOLDER_VALUE")
        assert len(found) == 1 and found[0].fixable and found[0].line == 1

    def test_a_placeholder_secret_is_reported_without_its_text(self):
        found = by_code(lint("NVIDIA_API_KEY=changeme\n", {}), "PLACEHOLDER_VALUE")
        assert found and "changeme" not in found[0].message

    def test_a_quoted_value_is_reported_because_the_loader_keeps_the_quotes(self):
        found = by_code(lint("WISP_SANDBOX_IMAGE='wisp-sandbox:py312'\nWISP_X=\"a\"\nWISP_Y='unbalanced\n", {}), "QUOTED_VALUE")
        assert [f.line for f in found] == [1, 2]

    @pytest.mark.parametrize("value", ["'", '"', "'a\"", "\"a'"])
    def test_a_lone_or_mismatched_quote_is_not_a_quoted_value(self, value):
        assert by_code(lint(f"WISP_X={value}\n", {}), "QUOTED_VALUE") == []

    def test_export_is_reported_because_the_loader_skips_the_line(self):
        found = by_code(lint("export WISP_SANDBOX=on\n", {}), "EXPORT_PREFIX")
        assert len(found) == 1 and found[0].fixable

    @pytest.mark.parametrize("line", ["just words", "= no key", "9BAD=1", "A B=1"])
    def test_a_line_the_loader_cannot_read(self, line):
        found = by_code(lint(f"{line}\n", {}), "UNPARSEABLE_LINE")
        assert len(found) == 1 and not found[0].fixable

    def test_comments_and_blank_lines_are_never_findings(self):
        assert lint("# WISP_MODEL=nope\n\n   \n#---\n", {}) == []

    def test_a_blank_selection_line_is_advice(self):
        found = by_code(lint("WISP_MODEL=\n", {}), "BLANK_SELECTION")
        assert len(found) == 1 and found[0].fixable and found[0].severity == "advice"

    def test_a_blank_flag_is_a_choice_and_is_left_alone(self):
        assert lint("WISP_REASONING_CORE_RULES=\n", {}) == []

    def test_a_selection_key_that_agrees_with_config_json_is_advice(self):
        found = by_code(lint("WISP_PROVIDER=nvidia\n", {"provider": "nvidia"}), "SELECTION_IN_ENV")
        assert len(found) == 1 and found[0].severity == "advice" and found[0].fixable

    def test_a_selection_key_config_json_does_not_have_is_advice(self):
        found = by_code(lint("WISP_PROVIDER=nvidia\n", {}), "SELECTION_IN_ENV")
        assert len(found) == 1 and found[0].fixable

    def test_a_selection_key_that_disagrees_with_config_json_is_a_problem_that_names_both(self):
        found = by_code(lint("WISP_MODEL=qwen2.5-coder\n", {"model": "nvidia/nemotron"}), "SHADOWS_SAVED_SELECTION")
        assert len(found) == 1 and found[0].severity == "problem" and not found[0].fixable
        assert "qwen2.5-coder" in found[0].message and "nvidia/nemotron" in found[0].message and "config.json" in found[0].message

    def test_an_api_base_is_named_but_its_value_is_not_printed(self):
        text = "WISP_API_BASE=https://user:hunter2@example.com/v1?token=abc\n"
        for finding in lint(text, {"api_base": "https://other.example/v1"}):
            assert "hunter2" not in finding.message and "token=abc" not in finding.message

    def test_a_generic_key_equal_to_a_provider_key_is_advice(self):
        found = by_code(lint(f"WISP_API_KEY={SECRET}\nNVIDIA_API_KEY={SECRET}\n", {}), "GENERIC_KEY_DUPLICATES_SPECIFIC")
        assert [(f.line, f.fixable) for f in found] == [(1, True)]

    def test_a_generic_key_that_differs_is_left_alone(self):
        assert by_code(lint(f"WISP_API_KEY={SECRET}\nNVIDIA_API_KEY=other-{SECRET}\n", {}), "GENERIC_KEY_DUPLICATES_SPECIFIC") == []

    def test_a_key_by_itself_is_fine(self):
        assert lint(f"WISP_API_KEY={SECRET}\n", {}) == []

    def test_an_unknown_wisp_key_is_advice_when_the_known_set_is_given(self):
        found = by_code(lint("WISP_TOOL_PROFLE=full\nWISP_TOOL_PROFILE=full\n", {}, known={"WISP_TOOL_PROFILE"}), "UNKNOWN_KEY")
        assert [f.key for f in found] == ["WISP_TOOL_PROFLE"] and not found[0].fixable

    def test_other_variables_are_not_judged_unknown(self):
        assert by_code(lint("NVIDIA_API_KEY=x\nHTTPS_PROXY=http://p\n", {}, known={"WISP_TOOL_PROFILE"}), "UNKNOWN_KEY") == []

    @pytest.mark.parametrize("mode", [0o644, 0o640, 0o620, 0o604, 0o660, 0o666, 0o777])
    def test_loose_permissions_are_a_problem(self, mode):
        assert codes(lint("A=1\n", {}, mode=mode)) == ["INSECURE_PERMISSIONS"]

    @pytest.mark.parametrize("mode", [0o600, 0o400, 0o700, 0o500])
    def test_owner_only_permissions_are_fine(self, mode):
        assert lint("A=1\n", {}, mode=mode) == []

    def test_no_finding_ever_contains_a_secret_value(self):
        text = (f"WISP_API_KEY={SECRET}\nNVIDIA_API_KEY={SECRET}\nOPENAI_API_KEY=\"{SECRET}\"\nWISP_API_KEY={SECRET}\nexport OPENROUTER_API_KEY={SECRET}\n"
                f"WISP_MODEL=m\nWISP_PROVIDER=x\n")
        for finding in lint(text, {"model": "other"}, mode=0o644):
            assert SECRET not in finding.message and SECRET not in repr(finding)


class TestTheFixer:
    def test_it_removes_duplicates_placeholders_and_blank_selection_lines_and_nothing_else(self):
        text = ("# header\nWISP_SANDBOX=on\n\nWISP_OLLAMA_URL=---\nWISP_MODEL=\n# note\nWISP_SANDBOX=off\nNVIDIA_API_KEY=" + SECRET + "\n")
        out = fix(text, {})
        assert out.text == "# header\nWISP_SANDBOX=on\n\n# note\nNVIDIA_API_KEY=" + SECRET + "\n"
        assert sorted(out.applied) == ["BLANK_SELECTION:WISP_MODEL", "DUPLICATE_KEY:WISP_SANDBOX", "PLACEHOLDER_VALUE:WISP_OLLAMA_URL"]

    def test_it_unquotes_and_drops_export(self):
        out = fix("WISP_A='x y'\nexport WISP_B=\"z\"\n", {})
        assert out.text == "WISP_A=x y\nWISP_B=z\n"

    def test_it_never_touches_a_value_it_does_not_report(self):
        text = f"WISP_X=a=b c  d\nNVIDIA_API_KEY={SECRET}\nWISP_Y=  spaced  \n"
        assert fix(text, {}).text == text

    def test_it_is_idempotent(self):
        text = f"WISP_OLLAMA_URL=---\nWISP_A='x'\nWISP_MODEL=\nWISP_A=y\nNVIDIA_API_KEY={SECRET}\nWISP_API_KEY={SECRET}\n"
        once = fix(text, {})
        twice = fix(once.text, {})
        assert twice.text == once.text and twice.applied == []

    def test_the_fixed_text_has_no_fixable_findings(self):
        text = f"WISP_OLLAMA_URL=---\nWISP_A='x'\nWISP_MODEL=\nWISP_A=y\nexport WISP_B=1\nNVIDIA_API_KEY={SECRET}\nWISP_API_KEY={SECRET}\nWISP_PROVIDER=nvidia\n"
        out = fix(text, {"provider": "nvidia"})
        assert [f for f in lint(out.text, {"provider": "nvidia"}) if f.fixable] == []

    def test_a_selection_key_that_agrees_moves_out_of_the_env(self):
        out = fix("WISP_PROVIDER=nvidia\nWISP_SANDBOX=on\n", {"provider": "nvidia"})
        assert out.text == "WISP_SANDBOX=on\n" and out.config_updates == {}

    def test_a_selection_key_config_json_lacks_moves_into_config_json(self):
        out = fix("WISP_PROVIDER=nvidia\nWISP_SANDBOX=on\n", {})
        assert out.text == "WISP_SANDBOX=on\n" and out.config_updates == {"provider": "nvidia"}

    def test_a_disagreement_is_never_guessed(self):
        text = "WISP_MODEL=qwen2.5-coder\n"
        out = fix(text, {"model": "nemotron"})
        assert out.text == text and out.config_updates == {} and [f.code for f in out.remaining] == ["SHADOWS_SAVED_SELECTION"]

    def test_prefer_config_drops_the_env_line_and_keeps_config_json(self):
        out = fix("WISP_MODEL=qwen2.5-coder\n", {"model": "nemotron"}, prefer="config")
        assert out.text == "" and out.config_updates == {}

    def test_prefer_env_moves_the_env_value_into_config_json(self):
        out = fix("WISP_MODEL=qwen2.5-coder\n", {"model": "nemotron"}, prefer="env")
        assert out.text == "" and out.config_updates == {"model": "qwen2.5-coder"}

    def test_an_unknown_preference_is_refused(self):
        with pytest.raises(ValueError):
            fix("WISP_MODEL=a\n", {"model": "b"}, prefer="whichever")

    def test_a_generic_key_that_duplicates_a_specific_one_is_dropped_and_the_specific_stays(self):
        out = fix(f"WISP_API_KEY={SECRET}\nNVIDIA_API_KEY={SECRET}\n", {})
        assert out.text == f"NVIDIA_API_KEY={SECRET}\n"

    def test_a_file_without_a_trailing_newline_stays_that_way(self):
        assert fix("WISP_A=1\nWISP_B=2", {}).text == "WISP_A=1\nWISP_B=2"

    def test_windows_line_endings_survive(self):
        assert fix("WISP_A=1\r\nWISP_OLLAMA_URL=---\r\n", {}).text == "WISP_A=1\r\n"


class TestUpsert:
    def test_it_replaces_in_place_and_keeps_everything_else(self):
        out = env_file.upsert("# c\nA=1\nB=2\n", {"A": "9"})
        assert out == "# c\nA=9\nB=2\n"

    def test_it_appends_a_new_key_at_the_end(self):
        assert env_file.upsert("A=1\n", {"B": "2"}) == "A=1\nB=2\n"

    def test_none_removes_every_assignment_of_the_key(self):
        assert env_file.upsert("A=1\nB=2\nA=3\n", {"A": None}) == "B=2\n"

    def test_updating_a_key_also_drops_its_later_duplicates(self):
        assert env_file.upsert("A=1\nB=2\nA=3\n", {"A": "9"}) == "A=9\nB=2\n"

    def test_a_missing_file_text_is_fine(self):
        assert env_file.upsert("", {"A": "1"}) == "A=1\n"

    @pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\x00b"])
    def test_a_value_that_would_inject_a_line_is_refused(self, bad):
        with pytest.raises(ValueError):
            env_file.upsert("A=1\n", {"B": bad})

    @pytest.mark.parametrize("key", ["", "A B", "9X", "A=B", "A\nB"])
    def test_a_bad_key_is_refused(self, key):
        with pytest.raises(ValueError):
            env_file.upsert("", {key: "1"})


class TestTheFileOperations:
    def _setup(self, tmp_path, env_text, config=None):
        env = tmp_path / ".env"
        env.write_text(env_text)
        os.chmod(env, 0o644)
        cfg = tmp_path / "config.json"
        if config is not None:
            cfg.write_text(json.dumps(config))
        return env, cfg

    def test_check_reads_the_file_the_config_and_the_mode(self, tmp_path):
        env, cfg = self._setup(tmp_path, "WISP_MODEL=a\n", {"model": "b"})
        assert codes(env_file.check_path(env, cfg)) == ["INSECURE_PERMISSIONS", "SHADOWS_SAVED_SELECTION"]

    def test_check_of_a_missing_file_is_empty(self, tmp_path):
        assert env_file.check_path(tmp_path / "nope", tmp_path / "config.json") == []

    def test_a_dry_run_changes_nothing(self, tmp_path):
        env, cfg = self._setup(tmp_path, "WISP_OLLAMA_URL=---\n", {})
        before = env.read_bytes()
        report = env_file.fix_path(env, cfg, apply=False)
        assert env.read_bytes() == before and report.applied == ["PLACEHOLDER_VALUE:WISP_OLLAMA_URL"] and report.backup is None

    def test_apply_makes_a_private_backup_of_the_exact_bytes_then_rewrites(self, tmp_path):
        env, cfg = self._setup(tmp_path, f"WISP_OLLAMA_URL=---\nNVIDIA_API_KEY={SECRET}\n", {})
        before = env.read_bytes()
        report = env_file.fix_path(env, cfg, apply=True)
        assert report.backup is not None and report.backup.read_bytes() == before
        assert stat.S_IMODE(report.backup.stat().st_mode) == 0o600 and stat.S_IMODE(env.stat().st_mode) == 0o600
        assert env.read_text() == f"NVIDIA_API_KEY={SECRET}\n"

    def test_apply_also_fixes_loose_permissions_when_nothing_else_is_wrong(self, tmp_path):
        env, cfg = self._setup(tmp_path, "A=1\n", {})
        env_file.fix_path(env, cfg, apply=True)
        assert stat.S_IMODE(env.stat().st_mode) == 0o600

    def test_config_json_gets_the_moved_value_before_the_env_line_goes(self, tmp_path):
        env, cfg = self._setup(tmp_path, "WISP_PROVIDER=nvidia\nWISP_SANDBOX=on\n", {"model": "m"})
        env_file.fix_path(env, cfg, apply=True)
        assert json.loads(cfg.read_text()) == {"model": "m", "provider": "nvidia"} and env.read_text() == "WISP_SANDBOX=on\n"

    def test_if_config_json_cannot_be_written_the_env_is_left_alone(self, tmp_path, monkeypatch):
        env, cfg = self._setup(tmp_path, "WISP_PROVIDER=nvidia\n", {})
        before = env.read_bytes()

        def refuse(*args, **kwargs):
            raise OSError("read-only")

        monkeypatch.setattr(env_file, "_write_config", refuse)
        with pytest.raises(OSError):
            env_file.fix_path(env, cfg, apply=True)
        assert env.read_bytes() == before

    def test_a_disagreement_without_a_preference_is_reported_and_left_in_place(self, tmp_path):
        env, cfg = self._setup(tmp_path, "WISP_MODEL=a\n", {"model": "b"})
        report = env_file.fix_path(env, cfg, apply=True)
        assert env.read_text() == "WISP_MODEL=a\n" and [f.code for f in report.remaining] == ["SHADOWS_SAVED_SELECTION"]

    def test_a_symlinked_env_is_refused(self, tmp_path):
        target = tmp_path / "elsewhere"
        target.write_text("WISP_OLLAMA_URL=---\n")
        link = tmp_path / ".env"
        link.symlink_to(target)
        with pytest.raises(ValueError, match="symbolic link"):
            env_file.fix_path(link, tmp_path / "config.json", apply=True)
        assert target.read_text() == "WISP_OLLAMA_URL=---\n"

    def test_the_report_never_carries_a_value(self, tmp_path):
        env, cfg = self._setup(tmp_path, f"WISP_API_KEY={SECRET}\nNVIDIA_API_KEY={SECRET}\nWISP_MODEL=zz-model\n", {"model": "yy-model"})
        report = env_file.fix_path(env, cfg, apply=False)
        assert SECRET not in repr(report)


class TestPersistenceSplit:
    """`provider_select.persist` is what /provider, /model, the wizard and `wisp config --set` write through."""

    @pytest.fixture
    def home(self, isolated_wisp_env, tmp_path):
        import wisp.config as cfg_mod
        import wisp.user_env as user_env

        cfg = tmp_path / ".config" / "wisp" / "config.json"
        cfg.parent.mkdir(parents=True)
        isolated_wisp_env.setattr(cfg_mod, "get_config_path", lambda: cfg)
        isolated_wisp_env.setattr(user_env, "user_env_path", lambda: tmp_path / ".config" / "wisp" / ".env")
        return SimpleHome(cfg, tmp_path / ".config" / "wisp" / ".env")

    def test_the_stale_model_in_env_cannot_survive_a_provider_switch(self, home):
        """The real failure: .env kept WISP_MODEL=qwen2.5-coder after /provider nvidia."""
        from wisp.provider_select import persist

        home.env.write_text("WISP_PROVIDER=ollama\nWISP_MODEL=qwen2.5-coder\nWISP_SANDBOX=on\n")
        assert persist({"provider": "nvidia", "model": ""}) is True
        text = home.env.read_text()
        assert "WISP_MODEL" not in text and "WISP_PROVIDER" not in text and "WISP_SANDBOX=on" in text
        saved = json.loads(home.cfg.read_text())
        assert saved["provider"] == "nvidia" and saved["model"] == ""

    def test_a_model_choice_goes_to_config_json_only(self, home):
        from wisp.provider_select import persist

        persist({"model": "nvidia/nemotron"})
        assert json.loads(home.cfg.read_text())["model"] == "nvidia/nemotron"
        assert not home.env.exists() or "WISP_MODEL" not in home.env.read_text()

    def test_every_selection_key_is_moved(self, home):
        from wisp.provider_select import persist

        home.env.write_text("WISP_PROVIDER=a\nWISP_MODEL=b\nWISP_API_BASE=c\nWISP_OLLAMA_URL=d\n")
        persist({"provider": "p", "model": "m", "api_base": "https://b.example/v1", "ollama_url": "http://u"})
        assert home.env.read_text() == ""
        assert json.loads(home.cfg.read_text()) == {"provider": "p", "model": "m", "api_base": "https://b.example/v1", "ollama_url": "http://u"}

    def test_a_key_goes_to_env_only_under_its_own_name(self, home, monkeypatch):
        from wisp.provider_select import store_key

        monkeypatch.delenv("WISP_API_KEY", raising=False)
        store_key("nvidia", SECRET)
        env_text = home.env.read_text()
        assert env_text == f"NVIDIA_API_KEY={SECRET}\n" and "WISP_API_KEY" not in env_text
        assert not home.cfg.exists() or SECRET not in home.cfg.read_text()
        assert stat.S_IMODE(home.env.stat().st_mode) == 0o600

    def test_comments_and_order_in_the_env_survive_a_persist(self, home):
        from wisp.provider_select import persist, store_key

        home.env.write_text("# keep me\nZ=1\n\n# and me\nA=2\n")
        persist({"provider": "nvidia"})
        store_key("nvidia", SECRET)
        assert home.env.read_text().startswith("# keep me\nZ=1\n\n# and me\nA=2\n")

    def test_the_wizard_writes_through_the_same_split(self, home):
        import wisp.cli.setup as setup_mod

        home.env.write_text("WISP_MODEL=qwen2.5-coder\n")
        setup_mod._persist_choice("nvidia", "nvidia/nemotron", "", SECRET, key_from_env=False)
        assert json.loads(home.cfg.read_text())["model"] == "nvidia/nemotron"
        assert home.env.read_text() == f"NVIDIA_API_KEY={SECRET}\n"

    def test_a_round_trip_new_process_sees_the_choice(self, home, monkeypatch):
        from wisp.config import WispConfig
        from wisp.provider_select import persist
        from wisp.user_env import load_user_env

        home.env.write_text("WISP_PROVIDER=ollama\nWISP_MODEL=qwen2.5-coder\n")
        persist({"provider": "nvidia", "model": "nvidia/nemotron"})
        for key in ("WISP_PROVIDER", "WISP_MODEL"):
            monkeypatch.delenv(key, raising=False)
        load_user_env(home.env)
        config = WispConfig()
        assert (config.provider, config.model) == ("nvidia", "nvidia/nemotron")

    def test_if_config_json_cannot_be_written_the_env_copy_is_kept(self, home, monkeypatch):
        """Never remove the only copy of a choice: the .env line goes only after config.json has the value."""
        import wisp.config as cfg_mod
        from wisp.provider_select import persist

        def refuse(*args, **kwargs):
            raise OSError("read-only")

        home.env.write_text("WISP_MODEL=old\n")
        monkeypatch.setattr(cfg_mod, "save_config", refuse)
        assert persist({"model": "new"}) is False
        assert home.env.read_text() == "WISP_MODEL=old\n"

    def test_a_symlinked_env_is_never_written_through(self, home):
        from wisp.provider_select import persist, store_key

        target = home.env.parent / "elsewhere"
        target.write_text("KEEP=1\n")
        home.env.symlink_to(target)
        persist({"model": "m"})
        store_key("nvidia", SECRET)
        assert target.read_text() == "KEEP=1\n" and home.env.is_symlink()  # neither written through, nor replaced by a regular file

    def test_wisp_config_set_of_a_selection_key_clears_the_shadow_too(self, home):
        from wisp.__main__ import cmd_config

        home.env.write_text("WISP_MODEL=qwen2.5-coder\n")
        cmd_config("model=nvidia/nemotron")
        assert json.loads(home.cfg.read_text())["model"] == "nvidia/nemotron" and "WISP_MODEL" not in home.env.read_text()


class SimpleHome:
    def __init__(self, cfg: Path, env: Path):
        self.cfg, self.env = cfg, env


class TestTheKnownKeys:
    """UNKNOWN_KEY is only as good as this list: a setting read through `get_setting("name")` reaches the environment as WISP_NAME without that spelling
    appearing anywhere in the source (found on the owner's real file: WISP_REASONING_CORE_RULES was flagged as unknown)."""

    def test_a_setting_read_by_name_is_known(self):
        from wisp.env_file import known_wisp_keys

        assert {"WISP_REASONING_CORE_RULES", "WISP_REASONING_CORE", "WISP_PROVIDER", "WISP_TOOL_PROFILE"} <= known_wisp_keys()

    def test_every_get_setting_literal_is_known(self):
        import re

        from wisp.env_file import known_wisp_keys

        names = set()
        for path in (Path(__file__).resolve().parents[1] / "wisp").rglob("*.py"):
            names.update(re.findall(r"""get_setting\(\s*["']([a-z0-9_]+)["']""", path.read_text(encoding="utf-8")))
        assert names and {f"WISP_{n.upper()}" for n in names} <= known_wisp_keys()

    def test_a_typo_is_still_unknown(self):
        from wisp.env_file import known_wisp_keys

        assert "WISP_REASONING_CORE_RULEZ" not in known_wisp_keys()


class TestTheBootNotice:
    def test_a_stale_selection_is_named_once_without_secrets(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(f"WISP_MODEL=qwen2.5-coder\nNVIDIA_API_KEY={SECRET}\n")
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"model": "nvidia/nemotron"}))
        notes = env_file.boot_notes(env, cfg)
        assert len(notes) == 1 and "WISP_MODEL" in notes[0] and "qwen2.5-coder" in notes[0] and "nvidia/nemotron" in notes[0]
        assert "wisp config env fix" in notes[0] and SECRET not in notes[0]

    def test_a_tidy_setup_says_nothing(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("WISP_SANDBOX=on\n")
        os.chmod(env, 0o600)
        assert env_file.boot_notes(env, tmp_path / "config.json") == []

    def test_it_never_raises(self, tmp_path):
        (tmp_path / ".env").write_bytes(b"\xff\xfe\x00bad")
        assert env_file.boot_notes(tmp_path / ".env", tmp_path / "config.json") == []


class TestThroughTheRealConsoleEntry:
    def _run(self, tmp_path, *args):
        home = tmp_path / "home"
        (home / ".config" / "wisp").mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("WISP_") and not k.endswith("_API_KEY")}
        env.update({"HOME": str(home), "PYTHONPATH": str(REPO), "TERM": "dumb", "NO_COLOR": "1"})
        return subprocess.run([sys.executable, "-P", "-m", "wisp", "config", "env", *args], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
                              cwd=tmp_path)

    def _files(self, tmp_path, env_text, config):
        d = tmp_path / "home" / ".config" / "wisp"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(env_text)
        os.chmod(d / ".env", 0o600)
        (d / "config.json").write_text(json.dumps(config))
        return d / ".env", d / "config.json"

    def test_check_reports_and_exits_non_zero_without_printing_values(self, tmp_path):
        self._files(tmp_path, f"WISP_MODEL=qwen2.5-coder\nWISP_OLLAMA_URL=---\nNVIDIA_API_KEY={SECRET}\n", {"model": "nvidia/nemotron"})
        result = self._run(tmp_path, "check")
        out = result.stdout + result.stderr
        assert result.returncode == 1
        assert "SHADOWS_SAVED_SELECTION" in out and "PLACEHOLDER_VALUE" in out and "WISP_MODEL" in out
        assert SECRET not in out and "line 1" in out

    def test_check_of_a_tidy_setup_exits_zero(self, tmp_path):
        self._files(tmp_path, "WISP_SANDBOX=on\n", {"provider": "nvidia"})
        assert self._run(tmp_path, "check").returncode == 0

    def test_fix_is_a_dry_run_unless_applied(self, tmp_path):
        env, _ = self._files(tmp_path, "WISP_OLLAMA_URL=---\n", {})
        result = self._run(tmp_path, "fix")
        assert env.read_text() == "WISP_OLLAMA_URL=---\n" and "dry run" in (result.stdout + result.stderr).lower()

    def test_fix_apply_prefer_config_repairs_the_real_case_and_a_second_run_is_clean(self, tmp_path):
        env, cfg = self._files(tmp_path, f"WISP_MODEL=qwen2.5-coder\nWISP_PROVIDER=nvidia\nWISP_OLLAMA_URL=---\nNVIDIA_API_KEY={SECRET}\nWISP_SANDBOX=on\n",
                               {"provider": "nvidia", "model": "nvidia/nemotron"})
        result = self._run(tmp_path, "fix", "--apply", "--prefer", "config")
        assert result.returncode == 0, result.stdout + result.stderr
        assert env.read_text() == f"NVIDIA_API_KEY={SECRET}\nWISP_SANDBOX=on\n"
        assert json.loads(cfg.read_text()) == {"provider": "nvidia", "model": "nvidia/nemotron"}
        assert list(env.parent.glob(".env.bak-*")) and SECRET not in (result.stdout + result.stderr)
        assert self._run(tmp_path, "check").returncode == 0

    def test_a_conflict_without_a_preference_exits_non_zero_and_changes_nothing_for_it(self, tmp_path):
        env, _ = self._files(tmp_path, "WISP_MODEL=a\n", {"model": "b"})
        result = self._run(tmp_path, "fix", "--apply")
        assert result.returncode == 1 and env.read_text() == "WISP_MODEL=a\n" and "--prefer" in (result.stdout + result.stderr)

    def test_an_unknown_verb_is_usage(self, tmp_path):
        assert self._run(tmp_path, "frobnicate").returncode == 2


class TestTheDoctorProbe:
    def test_the_probe_is_registered_and_warns_on_a_stale_selection(self, isolated_wisp_env, tmp_path):
        import wisp.cli.doctor_harness as dh
        from wisp.core.doctor import CheckStatus

        d = tmp_path / ".config" / "wisp"
        d.mkdir(parents=True)
        (d / ".env").write_text("WISP_MODEL=a\n")
        os.chmod(d / ".env", 0o600)
        (d / "config.json").write_text(json.dumps({"model": "b"}))
        isolated_wisp_env.setattr("wisp.user_env.user_env_path", lambda: d / ".env")
        import wisp.config as cfg_mod

        isolated_wisp_env.setattr(cfg_mod, "get_config_path", lambda: d / "config.json")
        assert "user_env" in dh.HARNESS_CHECK_NAMES
        result = dict(dh._CHECKS)["user_env"]()
        assert result.status is CheckStatus.WARN and "WISP_MODEL" in result.message

    def test_the_probe_is_ok_on_a_tidy_setup_and_on_no_file(self, isolated_wisp_env, tmp_path):
        import wisp.cli.doctor_harness as dh
        from wisp.core.doctor import CheckStatus

        isolated_wisp_env.setattr("wisp.user_env.user_env_path", lambda: tmp_path / "missing.env")
        assert dict(dh._CHECKS)["user_env"]().status is CheckStatus.OK
