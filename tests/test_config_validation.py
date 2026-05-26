"""Tests for WispConfig validation.

Verifies that invalid configs fail fast with clear error messages.
"""



class TestConfigValidation:

    def test_valid_config_no_errors(self):
        """A default WispConfig should be valid."""
        from wisp.config import WispConfig
        config = WispConfig()
        errors = config.validate()
        assert errors == []

    def test_temperature_out_of_range(self):
        """Temperature above max should fail validation."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(temperature=3.0)
        errors = config.validate()
        assert any("temperature" in e.lower() for e in errors)

    def test_temperature_negative(self):
        """Temperature below min should fail validation."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(temperature=-0.5)
        errors = config.validate()
        assert any("temperature" in e.lower() for e in errors)

    def test_max_iterations_too_low(self):
        """max_iterations below 1 should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(max_iterations=0)
        errors = config.validate()
        assert any("max_iterations" in e.lower() for e in errors)

    def test_max_iterations_too_high(self):
        """max_iterations above 100 should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(max_iterations=200)
        errors = config.validate()
        assert any("max_iterations" in e.lower() for e in errors)

    def test_max_reflections_negative(self):
        """max_reflections below 0 should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(max_reflections=-1)
        errors = config.validate()
        assert any("max_reflections" in e.lower() for e in errors)

    def test_max_context_tokens_too_low(self):
        """max_context_tokens below 1024 should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(max_context_tokens=512)
        errors = config.validate()
        assert any("max_context_tokens" in e.lower() for e in errors)

    def test_chars_per_token_too_low(self):
        """chars_per_token below 1 should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(chars_per_token=0)
        errors = config.validate()
        assert any("chars_per_token" in e.lower() for e in errors)

    def test_chars_per_token_too_high(self):
        """chars_per_token above 10 should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(chars_per_token=15)
        errors = config.validate()
        assert any("chars_per_token" in e.lower() for e in errors)

    def test_compact_threshold_too_low(self):
        """compact_threshold_tokens below 10 should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(compact_threshold_tokens=5)
        errors = config.validate()
        assert any("compact_threshold" in e.lower() for e in errors)

    def test_compact_threshold_too_high(self):
        """compact_threshold_tokens above 95 should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(compact_threshold_tokens=100)
        errors = config.validate()
        assert any("compact_threshold" in e.lower() for e in errors)

    def test_compact_keep_recent_too_low(self):
        """compact_keep_recent below 4 should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(compact_keep_recent=2)
        errors = config.validate()
        assert any("compact_keep_recent" in e.lower() for e in errors)

    def test_permission_mode_invalid(self):
        """permission_mode not in allowed values should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(permission_mode="invalid")
        errors = config.validate()
        assert any("permission_mode" in e.lower() for e in errors)

    def test_provider_empty(self):
        """Empty provider should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(provider="")
        errors = config.validate()
        assert any("provider" in e.lower() for e in errors)

    def test_model_empty(self):
        """Empty model should fail."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(model="")
        errors = config.validate()
        assert any("model" in e.lower() for e in errors)

    def test_multiple_errors(self):
        """Multiple invalid values should all be reported."""
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(temperature=5.0)
        config = config.replace(max_iterations=0)
        config = config.replace(permission_mode="bad")
        errors = config.validate()
        assert len(errors) >= 3

    def test_validate_returns_list(self):
        """validate() always returns a list."""
        from wisp.config import WispConfig
        config = WispConfig()
        result = config.validate()
        assert isinstance(result, list)
