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
