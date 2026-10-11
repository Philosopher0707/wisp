"""Tests for config schema validation."""

import pytest
from wisp.config import validate_config, save_config, load_config, get_schema


class TestValidateConfig:
    def test_valid_empty(self):
        """Empty config is valid."""
        assert validate_config({}) == []

    def test_valid_ollama_url(self):
        """Valid string settings pass."""
        assert validate_config({"ollama_url": "http://localhost:11434"}) == []

    def test_valid_temperature(self):
        """Valid float in range passes."""
        assert validate_config({"temperature": 0.5}) == []

    def test_invalid_temperature_type(self):
        """Temperature must be a number."""
        errors = validate_config({"temperature": "hot"})
        assert len(errors) == 1
        assert "temperature" in errors[0]

    def test_temperature_too_low(self):
        """Temperature below minimum fails."""
        errors = validate_config({"temperature": -1.0})
        assert len(errors) == 1
        assert "below minimum" in errors[0]

    def test_temperature_too_high(self):
        """Temperature above maximum fails."""
        errors = validate_config({"temperature": 3.0})
        assert len(errors) == 1
        assert "above maximum" in errors[0]

    def test_valid_max_iterations(self):
        """Valid int in range passes."""
        assert validate_config({"max_iterations": 50}) == []

    def test_max_iterations_too_low(self):
        """Max iterations below minimum fails."""
        errors = validate_config({"max_iterations": 0})
        assert len(errors) == 1

    def test_max_iterations_too_high(self):
        """Max iterations above maximum fails."""
        errors = validate_config({"max_iterations": 500})
        assert len(errors) == 1

    def test_valid_bool(self):
        """Valid bool passes."""
        assert validate_config({"auto_approve": False}) == []
        assert validate_config({"show_thinking": True}) == []

    def test_invalid_bool_type(self):
        """Bool must be actual bool, not string."""
        errors = validate_config({"auto_approve": "true"})
        assert len(errors) == 1
        assert "auto_approve" in errors[0]

    def test_unknown_key(self):
        """Unknown keys are reported."""
        errors = validate_config({"unknown_key": "value"})
        assert len(errors) == 1
        assert "Unknown" in errors[0]

    def test_valid_skill_dirs(self):
        """Valid list passes."""
        assert validate_config({"skill_dirs": [".agents/skills"]}) == []

    def test_invalid_skill_dirs_type(self):
        """Skill dirs must be a list."""
        errors = validate_config({"skill_dirs": ".agents/skills"})
        assert len(errors) == 1

    def test_valid_max_context_tokens(self):
        """Valid int above minimum passes."""
        assert validate_config({"max_context_tokens": 128000}) == []

    def test_max_context_tokens_too_low(self):
        """Context tokens below minimum fails."""
        errors = validate_config({"max_context_tokens": 100})
        assert len(errors) == 1

    def test_valid_chars_per_token(self):
        """Valid int in range passes."""
        assert validate_config({"chars_per_token": 4}) == []

    def test_chars_per_token_too_high(self):
        """Chars per token above maximum fails."""
        errors = validate_config({"chars_per_token": 20})
        assert len(errors) == 1

    def test_multiple_errors(self):
        """Multiple invalid values return multiple errors."""
        errors = validate_config({
            "temperature": "hot",
            "max_iterations": 0,
            "unknown": "x",
        })
        assert len(errors) >= 2


class TestSaveConfig:
    def test_save_valid(self, tmp_path, monkeypatch):
        """Saving valid config succeeds."""
        monkeypatch.setattr("wisp.config.WISP_CONFIG_DIR", tmp_path)
        save_config({"model": "test-model"})
        loaded = load_config()
        assert loaded["model"] == "test-model"

    def test_save_invalid_raises(self, tmp_path, monkeypatch):
        """Saving invalid config raises ValueError."""
        monkeypatch.setattr("wisp.config.WISP_CONFIG_DIR", tmp_path)
        with pytest.raises(ValueError, match="Cannot save"):
            save_config({"temperature": "invalid"})


class TestGetSchema:
    def test_schema_has_all_keys(self):
        """Schema contains all expected settings."""
        schema = get_schema()
        expected_keys = {
            "ollama_url", "model", "temperature", "max_tokens",
            "skill_dirs", "workspace", "auto_approve", "show_thinking",
            "max_iterations", "max_context_tokens", "chars_per_token",
        }
        assert expected_keys.issubset(schema.keys())

    def test_schema_has_types(self):
        """Each schema entry has a type."""
        schema = get_schema()
        for key, info in schema.items():
            assert "type" in info, f"{key} missing type"
            assert "default" in info, f"{key} missing default"
            assert "description" in info, f"{key} missing description"


class TestToolPoolSizeSettings:
    """GH#7.4: pool sizes are validated, documented settings — not bare getattr."""

    def test_valid_pool_sizes(self):
        assert validate_config({"tool_pool_size": 8}) == []
        assert validate_config({"tool_pool_network_size": 4}) == []

    def test_pool_size_range_enforced(self):
        assert validate_config({"tool_pool_size": 0})
        assert validate_config({"tool_pool_network_size": 99})

    def test_pool_size_type_enforced(self):
        errors = validate_config({"tool_pool_size": "many"})
        assert len(errors) == 1 and "tool_pool_size" in errors[0]

    def test_schema_declares_env_vars(self):
        schema = get_schema()
        assert schema["tool_pool_size"]["env_var"] == "WISP_TOOL_POOL_SIZE"
        assert schema["tool_pool_network_size"]["env_var"] == "WISP_TOOL_POOL_NETWORK_SIZE"

    def test_config_reads_env_and_clamps(self, monkeypatch):
        from wisp.config import WispConfig

        monkeypatch.setenv("WISP_TOOL_POOL_SIZE", "3")
        monkeypatch.setenv("WISP_TOOL_POOL_NETWORK_SIZE", "2")
        cfg = WispConfig()
        assert cfg.tool_pool_size == 3
        assert cfg.tool_pool_network_size == 2
        monkeypatch.setenv("WISP_TOOL_POOL_SIZE", "0")
        assert WispConfig().tool_pool_size == 1, "out-of-range clamps to min"
        monkeypatch.setenv("WISP_TOOL_POOL_SIZE", "notanint")
        assert WispConfig().tool_pool_size == 8, "unparseable falls back to default"


class TestDiffMaxLinesSetting:
    """diff_max_lines follows the same int-setting convention as the pool sizes."""

    def test_schema_declares_env_var_and_default(self):
        schema = get_schema()
        assert schema["diff_max_lines"]["env_var"] == "WISP_DIFF_MAX_LINES"
        assert schema["diff_max_lines"]["default"] == 50

    def test_negative_rejected_by_validation(self):
        assert validate_config({"diff_max_lines": -1})

    def test_config_reads_env(self, monkeypatch):
        from wisp.config import WispConfig

        monkeypatch.delenv("WISP_DIFF_MAX_LINES", raising=False)
        assert WispConfig().diff_max_lines == 50
        monkeypatch.setenv("WISP_DIFF_MAX_LINES", "0")
        assert WispConfig().diff_max_lines == 0, "0 means unlimited"
        monkeypatch.setenv("WISP_DIFF_MAX_LINES", "200")
        assert WispConfig().diff_max_lines == 200


class TestGraphModelTiersSetting:
    """`graph_model_tiers`: which real model each graph node tier (`cheap | standard | strong`) means, as `cheap=MODEL,strong=MODEL`.

    Empty by default, and then nothing changes: every tier is the session model. Same provider as the session; a tier left out is the session model."""

    def test_the_schema_declares_it_with_its_environment_variable(self):
        entry = get_schema()["graph_model_tiers"]
        assert entry["type"] is str and entry["default"] == "" and entry["env_var"] == "WISP_GRAPH_MODEL_TIERS"
        assert "cheap=" in entry["description"] and "session" in entry["description"]

    def test_it_is_empty_by_default(self, isolated_wisp_env):
        from wisp.config import WispConfig

        assert WispConfig().graph_model_tiers == ""

    def test_the_environment_sets_it_in_the_canonical_form(self, isolated_wisp_env, monkeypatch):
        from wisp.config import WispConfig

        monkeypatch.setenv("WISP_GRAPH_MODEL_TIERS", " strong = big-model , cheap=tiny-model ")
        assert WispConfig().graph_model_tiers == "cheap=tiny-model,strong=big-model"

    def test_the_config_file_sets_it(self, isolated_wisp_env, monkeypatch, tmp_path):
        import wisp.config as cfg_mod
        from wisp.config import WispConfig

        path = tmp_path / "config.json"
        path.write_text('{"graph_model_tiers": "cheap=file-model"}')
        monkeypatch.setattr(cfg_mod, "get_config_path", lambda: path)
        monkeypatch.delenv("WISP_GRAPH_MODEL_TIERS", raising=False)
        assert WispConfig().graph_model_tiers == "cheap=file-model"

    def test_the_config_command_can_set_and_validate_it_for_a_later_session(self, tmp_path):
        """The way it will be set later: the real `wisp config --set` in a throwaway HOME, then a fresh process reads it."""
        import json
        import os
        import subprocess
        import sys
        from pathlib import Path

        repo = str(Path(__file__).resolve().parents[1])
        env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": repo + os.pathsep + os.environ.get("PYTHONPATH", "")}
        env.pop("WISP_GRAPH_MODEL_TIERS", None)

        def python(*args):
            return subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=120, env=env, cwd=tmp_path)

        assert python("-m", "wisp", "config", "--set", "graph_model_tiers=strong=big-model, cheap=tiny-model").returncode == 0
        written = json.loads((tmp_path / ".config" / "wisp" / "config.json").read_text())
        assert written["graph_model_tiers"] == "strong=big-model, cheap=tiny-model"
        assert "valid" in python("-m", "wisp", "config", "--validate").stdout
        read = python("-c", "from wisp.config import WispConfig; print(WispConfig().graph_model_tiers)")
        assert read.stdout.strip().splitlines()[-1] == "cheap=tiny-model,strong=big-model"

    def test_the_environment_beats_the_file(self, isolated_wisp_env, monkeypatch, tmp_path):
        import wisp.config as cfg_mod
        from wisp.config import WispConfig

        path = tmp_path / "config.json"
        path.write_text('{"graph_model_tiers": "cheap=file-model"}')
        monkeypatch.setattr(cfg_mod, "get_config_path", lambda: path)
        monkeypatch.setenv("WISP_GRAPH_MODEL_TIERS", "cheap=env-model")
        assert WispConfig().graph_model_tiers == "cheap=env-model"

    def test_a_typo_or_an_unknown_tier_is_dropped_never_guessed(self, isolated_wisp_env, monkeypatch):
        from wisp.config import WispConfig

        monkeypatch.setenv("WISP_GRAPH_MODEL_TIERS", "ultra=nope,cheap=ok,stronk=nope,strong")
        assert WispConfig().graph_model_tiers == "cheap=ok"

    def test_validate_config_takes_a_string_and_refuses_other_types(self):
        assert validate_config({"graph_model_tiers": "cheap=a,strong=b"}) == []
        assert len(validate_config({"graph_model_tiers": 5})) == 1


class TestParseModelTiers:
    @staticmethod
    def parse(raw):
        from wisp.graph.tiers import parse_model_tiers

        return parse_model_tiers(raw)

    @pytest.mark.parametrize("raw", ["", "   ", None, ",", " , ,", 5, [], {}])
    def test_nothing_set_is_an_empty_map(self, raw):
        assert self.parse(raw) == {}

    def test_pairs_with_spaces(self):
        assert self.parse(" cheap = a , strong=b ") == {"cheap": "a", "strong": "b"}

    @pytest.mark.parametrize("name", ["nvidia/nemotron-3-ultra-550b-a55b", "qwen2.5-coder:7b", "meta-llama/llama-3.1-8b-instruct:free", "gpt_4o.mini", "org/m@v1+x"])
    def test_real_model_names_pass(self, name):
        assert self.parse(f"cheap={name}") == {"cheap": name}

    def test_a_name_of_exactly_the_longest_allowed_length_passes_and_one_more_does_not(self):
        assert self.parse("cheap=" + "m" * 128) == {"cheap": "m" * 128}
        assert self.parse("cheap=" + "m" * 129) == {}

    @pytest.mark.parametrize("bad", ["cheap=-x", "cheap=.x", "cheap=/x", "cheap=:x", "cheap=_x", "cheap=@x"])
    def test_a_model_name_starts_with_a_letter_or_a_digit(self, bad):
        assert self.parse(bad) == {}

    def test_a_name_may_start_with_a_digit(self):
        assert self.parse("cheap=4o-mini") == {"cheap": "4o-mini"}

    def test_dropping_entries_is_logged_by_count_and_never_quotes_what_was_typed(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="wisp.graph.tiers"):
            assert self.parse("cheap=ok,strong=sk-LOOKS-LIKE-A-KEY with spaces,ultra=x") == {"cheap": "ok"}
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "ignored 2 entries" in text and "sk-LOOKS" not in text and "ultra" not in text

    def test_one_dropped_entry_is_worded_in_the_singular(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="wisp.graph.tiers"):
            self.parse("cheap=a b")
        assert "ignored 1 entry" in " ".join(r.getMessage() for r in caplog.records)

    def test_nothing_dropped_logs_nothing(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="wisp.graph.tiers"):
            self.parse("cheap=a,strong=b")
            self.parse("")
        assert caplog.records == []

    def test_a_piece_that_is_not_a_pair_counts_as_dropped(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="wisp.graph.tiers"):
            assert self.parse("cheap=a,oops") == {"cheap": "a"}
        assert "ignored 1 entry" in " ".join(r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("raw", ['["cheap", "a"]', '"cheap=a"', "{not json"])
    def test_json_that_is_not_an_object_is_an_empty_map(self, raw):
        assert self.parse(raw) == {}

    def test_a_broken_json_object_is_logged_as_dropped(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="wisp.graph.tiers"):
            self.parse("{not json")
        assert "ignored 1 entry" in " ".join(r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("bad", ["cheap", "=a", "cheap=", "cheap=a b", "cheap=a;rm -rf /", "cheap=$(x)", "cheap=a\nb", "cheap=`x`", "cheap=" + "m" * 129, "cheap=../../x", "cheap=a/../b", "cheap=a|b", "cheap=a&b", "cheap='a'"])
    def test_malformed_entries_are_dropped_not_repaired(self, bad):
        assert self.parse(bad) == {}

    def test_one_bad_entry_does_not_take_the_good_ones_down(self):
        assert self.parse("cheap=a b,strong=ok") == {"strong": "ok"}

    def test_unknown_tiers_are_dropped(self):
        assert self.parse("ultra=x,cheap=a") == {"cheap": "a"}

    def test_tier_names_are_case_insensitive_and_model_names_keep_their_case(self):
        assert self.parse("CHEAP=Tiny-Model") == {"cheap": "Tiny-Model"}

    def test_a_later_entry_for_the_same_tier_wins(self):
        assert self.parse("cheap=a,cheap=b") == {"cheap": "b"}

    def test_a_mapping_is_accepted_and_filtered_the_same_way(self):
        assert self.parse({"cheap": "a", "bogus": "b", "strong": 5, "standard": "c d"}) == {"cheap": "a"}

    def test_a_json_object_string_is_accepted(self):
        assert self.parse('{"cheap": "a", "strong": "b"}') == {"cheap": "a", "strong": "b"}

    def test_broken_json_is_an_empty_map(self):
        assert self.parse('{"cheap": "a"') == {}

    def test_a_trailing_or_doubled_comma_is_not_an_error_and_logs_nothing(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="wisp.graph.tiers"):
            assert self.parse("cheap=a,") == {"cheap": "a"}
            assert self.parse(",cheap=a,,strong=b,") == {"cheap": "a", "strong": "b"}
        assert caplog.records == []

    def test_a_json_list_is_dropped_and_logged(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="wisp.graph.tiers"):
            assert self.parse('["cheap", "a"]') == {}
        assert "ignored" in " ".join(r.getMessage() for r in caplog.records)

    def test_render_leaves_out_what_is_not_a_tier_and_what_is_empty(self):
        from wisp.graph.tiers import render_model_tiers

        assert render_model_tiers({"bogus": "x", "cheap": "a"}) == "cheap=a"
        assert render_model_tiers({"cheap": "", "strong": "b"}) == "strong=b"

    def test_render_is_canonical_and_round_trips(self):
        from wisp.graph.tiers import render_model_tiers

        mapping = {"strong": "b", "cheap": "a", "standard": "m"}
        assert render_model_tiers(mapping) == "cheap=a,standard=m,strong=b"
        assert self.parse(render_model_tiers(mapping)) == mapping
        assert render_model_tiers({}) == ""


class TestAnEmptyModelVariableDoesNotShadowTheSavedModel:
    """`WISP_MODEL=` (a line left empty in `.env`) used to beat the model saved in config.json, so Wisp served "the first model the provider serves"
    (alphabetically the first model of the NVIDIA catalog, one the account was not entitled to) and every turn answered 404."""

    def _save(self, monkeypatch, tmp_path, model="saved/model"):
        import json
        monkeypatch.setattr("wisp.config.WISP_CONFIG_DIR", tmp_path)
        (tmp_path / "config.json").write_text(json.dumps({"model": model}))

    def test_an_empty_variable_leaves_the_saved_model_in_force(self, monkeypatch, tmp_path):
        from wisp.config import WispConfig
        self._save(monkeypatch, tmp_path)
        monkeypatch.setenv("WISP_MODEL", "")
        assert WispConfig().model == "saved/model"

    def test_a_blank_variable_is_empty_too(self, monkeypatch, tmp_path):
        from wisp.config import WispConfig
        self._save(monkeypatch, tmp_path)
        monkeypatch.setenv("WISP_MODEL", "   ")
        assert WispConfig().model == "saved/model"

    def test_a_set_variable_still_wins_over_the_saved_model(self, monkeypatch, tmp_path):
        from wisp.config import WispConfig
        self._save(monkeypatch, tmp_path)
        monkeypatch.setenv("WISP_MODEL", "env/model")
        assert WispConfig().model == "env/model"

    def test_nothing_anywhere_is_still_the_unset_model(self, monkeypatch, tmp_path):
        from wisp.config import DEFAULT_MODEL, WispConfig
        monkeypatch.setattr("wisp.config.WISP_CONFIG_DIR", tmp_path)
        monkeypatch.setenv("WISP_MODEL", "")
        assert WispConfig().model == DEFAULT_MODEL
