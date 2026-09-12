"""PHASE 13-I0 — malformed disable-model-invocation contract.

Strict 4-state advertisement eligibility. Tests assert on the REAL
parsed values (PyYAML coercion verified: yes/no/on/off → bool,
1/0 → int, quoted → str, null/empty → None).
"""
from __future__ import annotations

import pytest
import yaml

from wisp.skills import (
    VISIBILITY_ABSENT,
    VISIBILITY_DISABLED,
    VISIBILITY_ENABLED,
    VISIBILITY_INVALID,
    is_model_advertisable,
    parse_skill,
    resolve_invocation_visibility,
)


def _skill_file(tmp_path, field_line=""):
    d = tmp_path / ".agents" / "skills" / "probe"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: probe\ndescription: Probe helper.\n"
        + field_line + "---\n\nBody.\n")
    return d / "SKILL.md"


def _advertised(tmp_path, field_line=""):
    from wisp.extensions.skills import SkillExtension
    skill = parse_skill(_skill_file(tmp_path, field_line))
    assert skill is not None
    ext = SkillExtension(workspace=str(tmp_path))
    ext._skills = [skill]
    return [t["function"]["name"] for t in ext.tools()]


# ── Valid states ──

def test_absent_preserves_existing_behavior(tmp_path):
    assert "skill__probe" in _advertised(tmp_path)


def test_boolean_true_hidden(tmp_path):
    assert "skill__probe" not in _advertised(
        tmp_path, "disable-model-invocation: true\n")


def test_boolean_false_visible(tmp_path):
    assert "skill__probe" in _advertised(
        tmp_path, "disable-model-invocation: false\n")


# ── Resolver unit contract ──

def test_resolver_states():
    from wisp.skills import _ABSENT
    assert resolve_invocation_visibility(_ABSENT) == VISIBILITY_ABSENT
    assert resolve_invocation_visibility(True) == VISIBILITY_DISABLED
    assert resolve_invocation_visibility(False) == VISIBILITY_ENABLED
    assert is_model_advertisable(VISIBILITY_ABSENT) is True
    assert is_model_advertisable(VISIBILITY_ENABLED) is True
    assert is_model_advertisable(VISIBILITY_DISABLED) is False
    assert is_model_advertisable(VISIBILITY_INVALID) is False


# ── Malformed strings: all hidden ──

@pytest.mark.parametrize("text", [
    '"true"', '"false"', "'true'", "'false'",
    '"TRUE"', '"FALSE"', '"yes"', '"no"', '"random"',
])
def test_malformed_strings_hidden(tmp_path, text, caplog):
    import logging
    assert yaml.safe_load(f"k: {text}")["k"] == text.strip("\"'")
    assert "skill__probe" not in _advertised(
        tmp_path, f"disable-model-invocation: {text}\n")
    assert any("malformed" in r.message and "disable-model-invocation" in r.message
               for r in caplog.records if r.levelno >= logging.WARNING)


def test_plain_yes_no_follow_parser_coercion():
    # PyYAML coerces yes/no to bool — contract classifies parsed values.
    assert yaml.safe_load("k: yes")["k"] is True
    assert yaml.safe_load("k: no")["k"] is False
    assert resolve_invocation_visibility(True) == VISIBILITY_DISABLED
    assert resolve_invocation_visibility(False) == VISIBILITY_ENABLED


# ── Malformed numbers / null / collections: all hidden ──

@pytest.mark.parametrize("text", ["0", "1", "2", "null", "~", "[]", "{}"])
def test_malformed_scalars_collections_hidden(tmp_path, text):
    parsed = yaml.safe_load(f"k: {text}")["k"]
    assert type(parsed) is not bool  # sanity: not an actual boolean
    assert "skill__probe" not in _advertised(
        tmp_path, f"disable-model-invocation: {text}\n")


# ── Mandatory adversarial regression: string "false" ──

def test_adversarial_string_false_not_invocable(tmp_path):
    """'"false"' must NEVER mean enabled: naive truthiness (`if value:`)
    would advertise it. Strict type check hides it."""
    assert yaml.safe_load('k: "false"')["k"] == "false"
    assert type(yaml.safe_load('k: "false"')["k"]) is str
    assert "skill__probe" not in _advertised(
        tmp_path, 'disable-model-invocation: "false"\n')


def test_adversarial_string_true_no_other_failure(tmp_path):
    assert "skill__probe" not in _advertised(
        tmp_path, 'disable-model-invocation: "true"\n')


# ── ints are not bools (bool subclasses int — isinstance would lie) ──

@pytest.mark.parametrize("value", [0, 1, True, False])
def test_int_bool_distinction(value):
    state = resolve_invocation_visibility(value)
    if type(value) is bool:
        assert state in (VISIBILITY_DISABLED, VISIBILITY_ENABLED)
    else:
        assert state == VISIBILITY_INVALID
