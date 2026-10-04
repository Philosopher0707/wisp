"""A skill may ask for its full instructions in the system prompt (`inline-instructions: true`).

Why: the skills block shows each skill's description and only the first 200 characters of its body. The
full text is behind the `skill__<name>` tool, which `read_only` refuses, so a read-only session (the
network agent's evaluation and its diagnose/propose tiers) never saw the orchestrator's loop, thresholds
or escalation rules. Removing the cut for every skill is not an option: a user with dozens of skills
would add tens of thousands of tokens to every request. So it is opt-in, per skill, and fail-closed:
only a real YAML boolean `true` counts (the same rule as `disable-model-invocation`).

The observation point is the production one: `WispAgentCore._build_system_prompt`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from wisp import skills as skills_mod
from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.skills import parse_skill

BODY_HEAD = "HEAD-" + "x" * 195
BODY = BODY_HEAD + "\n" + "filler line\n" * 150


@pytest.fixture(autouse=True)
def _no_global_skills(monkeypatch):
    monkeypatch.setattr(skills_mod, "GLOBAL_SKILL_DIRS", [])


def _skill(root, name, body, flag=None):
    d = root / ".agents" / "skills" / name
    d.mkdir(parents=True)
    front = f"name: {name}\ndescription: test skill {name}\n"
    if flag is not None:
        front += f"inline-instructions: {flag}\n"
    (d / "SKILL.md").write_text(f"---\n{front}---\n{body}\nTAIL-{name}\n")
    return d / "SKILL.md"


def _prompt(workspace) -> str:
    cfg = WispConfig().replace(model="m", provider="mock", workspace=str(workspace), permission_mode="read_only")
    core = WispAgentCore(config=cfg, provider=MagicMock())
    return core._build_system_prompt({"id": "s", "workspace": str(workspace), "messages": []}, query=None)


# ── parsing: only a real boolean true opts in ────────────────────────────────

def test_true_opts_in_and_absence_does_not(tmp_path):
    assert parse_skill(_skill(tmp_path, "optin", BODY, "true")).inline_instructions is True
    assert parse_skill(_skill(tmp_path, "default", BODY)).inline_instructions is False
    assert parse_skill(_skill(tmp_path, "optout", BODY, "false")).inline_instructions is False


@pytest.mark.parametrize("raw", ['"true"', "1", "yes please", "[true]", "null"])
def test_anything_but_a_boolean_true_is_not_an_opt_in(tmp_path, raw):
    assert parse_skill(_skill(tmp_path, "odd", BODY, raw)).inline_instructions is False


# ── the prompt the model actually receives ───────────────────────────────────

def test_an_opted_in_skill_is_in_the_prompt_in_full_and_others_are_still_cut(tmp_path):
    _skill(tmp_path, "full", BODY, "true")
    _skill(tmp_path, "plain", BODY)
    prompt = _prompt(tmp_path)
    assert "TAIL-full" in prompt
    assert BODY_HEAD in prompt and "TAIL-plain" not in prompt, "a skill that did not ask keeps the 200-character cut"


def test_a_malformed_opt_in_keeps_the_cut(tmp_path):
    _skill(tmp_path, "sneaky", BODY, '"true"')
    assert "TAIL-sneaky" not in _prompt(tmp_path)


# ── the shipped network skills ───────────────────────────────────────────────

def test_every_shipped_network_skill_reaches_the_model_in_full(tmp_path):
    from wisp_net import evaluation as ev

    case = ev.CASES[0]
    _home, workspace = ev.prepare(tmp_path, case)
    prompt = _prompt(workspace)
    for path in sorted(ev.AGENTS.glob("*/SKILL.md")):
        skill = parse_skill(path)
        assert skill is not None and skill.inline_instructions, f"{path.parent.name} does not opt in"
        last_line = skill.instructions.strip().splitlines()[-1]
        assert last_line in prompt, f"{path.parent.name}: the end of its instructions is not in the prompt"


def test_the_inlined_network_skills_stay_small():
    """Inlining is paid for in every request; keep the shipped set well under a few thousand tokens."""
    from wisp_net import evaluation as ev

    total = sum(len(parse_skill(p).instructions) for p in ev.AGENTS.glob("*/SKILL.md"))
    assert total < 10_000, total
