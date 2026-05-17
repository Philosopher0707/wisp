"""Tests for wisp.long_horizon.templates — task template matching and application.

Covers: template matching, application, listing, and edge cases.
"""

from __future__ import annotations

import pytest

from wisp.long_horizon.templates import (
    match_template,
    apply_template,
    list_templates,
    TEMPLATES,
)
from wisp.long_horizon.state import Step


# ══════════════════════════════════════════════════════════════════════
# Template matching
# ══════════════════════════════════════════════════════════════════════

class TestMatchTemplate:
    def test_migrate_flask_to_fastapi(self):
        key = match_template("Migrate Flask app to FastAPI")
        assert key == "migrate_flask_to_fastapi"

    def test_add_authentication(self):
        key = match_template("Add JWT authentication to the API")
        assert key == "add_authentication"

    def test_refactor_module(self):
        key = match_template("Refactor the auth module")
        assert key == "refactor_module"

    def test_add_tests(self):
        key = match_template("Add tests for the payment module")
        assert key == "add_tests"

    def test_dockerize(self):
        key = match_template("Dockerize the application")
        assert key == "dockerize"

    def test_setup_ci_cd(self):
        key = match_template("Set up GitHub Actions CI/CD")
        assert key == "setup_ci_cd"

    def test_no_match(self):
        key = match_template("Fix typo in README")
        assert key is None

    def test_case_insensitive(self):
        key = match_template("MIGRATE FLASK TO FASTAPI")
        assert key == "migrate_flask_to_fastapi"


# ══════════════════════════════════════════════════════════════════════
# Template application
# ══════════════════════════════════════════════════════════════════════

class TestApplyTemplate:
    def test_returns_steps(self):
        steps = apply_template("add_tests", "Add tests for payment")
        assert len(steps) > 0
        assert all(isinstance(s, Step) for s in steps)

    def test_step_descriptions_preserved(self):
        steps = apply_template("dockerize", "Dockerize my app")
        # At least one step should mention docker
        assert any("docker" in s.description.lower() for s in steps)

    def test_steps_are_copies(self):
        steps1 = apply_template("refactor_module", "Refactor A")
        steps2 = apply_template("refactor_module", "Refactor B")
        # Should be independent copies
        assert steps1 is not steps2
        assert steps1[0] is not steps2[0]

    def test_unknown_template_returns_empty(self):
        steps = apply_template("nonexistent", "Some goal")
        assert steps == []


# ══════════════════════════════════════════════════════════════════════
# Template listing
# ══════════════════════════════════════════════════════════════════════

class TestListTemplates:
    def test_returns_all_templates(self):
        templates = list_templates()
        assert len(templates) == len(TEMPLATES)

    def test_has_required_fields(self):
        templates = list_templates()
        for t in templates:
            assert "key" in t
            assert "name" in t
            assert "steps" in t
            assert isinstance(t["steps"], int)
            assert t["steps"] > 0

    def test_name_is_title_case(self):
        templates = list_templates()
        for t in templates:
            assert t["name"][0].isupper()


# ══════════════════════════════════════════════════════════════════════
# Integration with runner
# ══════════════════════════════════════════════════════════════════════

class TestTemplateIntegration:
    def test_all_templates_have_steps(self):
        for key, steps in TEMPLATES.items():
            assert len(steps) > 0, f"Template {key} has no steps"
            for step in steps:
                assert step.id, f"Step in {key} missing id"
                assert step.description, f"Step in {key} missing description"

    def test_template_steps_have_unique_ids(self):
        for key, steps in TEMPLATES.items():
            ids = [s.id for s in steps]
            assert len(ids) == len(set(ids)), f"Template {key} has duplicate step ids"
