"""Tests for skill capture — recording, repetition detection, SKILL.md output."""

import pytest

from wisp.skill_capture import (
    CapturedStep,
    SkillCapture,
    _digest_args,
    get_capture,
    reset_capture,
)


@pytest.fixture(autouse=True)
def _fresh_singleton():
    reset_capture()
    yield
    reset_capture()


class TestArgDigest:
    def test_volatile_keys_elided(self):
        digest = _digest_args({"path": "a.py", "content": "x" * 5000})
        assert digest["content"] == "<5000 chars>"
        assert digest["path"] == "a.py"

    def test_long_values_truncated(self):
        digest = _digest_args({"command": "y" * 200})
        assert len(digest["command"]) == 60
        assert digest["command"].endswith("...")


class TestRecorder:
    def test_record_and_recent(self):
        cap = SkillCapture()
        cap.record("read_file", {"path": "a.py"})
        cap.record("run_tests")
        recent = cap.recent(10)
        assert [s.tool for s in recent] == ["read_file", "run_tests"]
        assert recent[0].args == {"path": "a.py"}

    def test_subagent_chatter_not_recorded(self):
        cap = SkillCapture()
        cap.record("subagent_list")
        cap.record("orchestrate_vote", {})
        cap.record("read_file")
        assert [s.tool for s in cap.recent(5)] == ["read_file"]

    def test_maxlen_rolls(self):
        cap = SkillCapture(maxlen=3)
        for i in range(6):
            cap.record(f"tool_{i}")
        assert [s.tool for s in cap.recent(10)] == ["tool_3", "tool_4", "tool_5"]


class TestSuggest:
    def test_detects_repeated_tail(self):
        cap = SkillCapture()
        workflow = ["read_file", "edit_file", "run_tests"]
        # Run the same 3-step dance twice.
        for _ in range(2):
            for t in workflow:
                cap.record(t)
        suggestion = cap.suggest()
        assert suggestion is not None
        assert [s.tool for s in suggestion.steps] == workflow
        assert suggestion.occurrences >= 2

    def test_no_false_positive_without_repeats(self):
        cap = SkillCapture()
        for t in ("read_file", "edit_file", "run_tests", "git_commit"):
            cap.record(t)
        assert cap.suggest() is None

    def test_prefers_longest_repeating_window(self):
        cap = SkillCapture()
        seq = ["a", "b", "c"]
        for _ in range(2):
            for t in seq:
                cap.record(t)
        suggestion = cap.suggest(max_window=5)
        assert len(suggestion.steps) == 3

    def test_empty_history_returns_none(self):
        assert SkillCapture().suggest() is None

    def test_steps_carry_tail_digests(self):
        cap = SkillCapture()
        for i in range(2):
            cap.record("read_file", {"path": f"file_round_{i}.py"})
            cap.record("edit_file", {"path": f"file_round_{i}.py"})
        suggestion = cap.suggest(min_repeats=2)
        assert suggestion is not None
        # Digests come from the TAIL round — realistic recent examples.
        assert suggestion.steps[-1].args["path"] == "file_round_1.py"
        assert "file_round_1.py" in json_of(suggestion)

    def test_degenerate_history_below_min_window_returns_none(self):
        cap = SkillCapture()
        cap.record("read_file")
        cap.record("read_file")
        # Two identical single calls are chatter, not a workflow.
        assert cap.suggest() is None


def json_of(suggestion) -> str:
    return str([s.describe() for s in suggestion.steps])


class TestRenderSkill:
    def test_writes_discoverable_skill_md(self, tmp_path):
        from wisp.skills import discover_skills
        cap = SkillCapture()
        cap.record("read_file", {"path": "src/auth.py"})
        cap.record("edit_file", {"path": "src/auth.py"})
        path, merged = cap.render_skill("fix-auth", "Fix the auth bug", str(tmp_path))

        assert path.exists()
        assert merged is False
        assert path.parent.name == "fix-auth"
        skills = discover_skills(str(tmp_path))
        assert any(s.name == "fix-auth" for s in skills)
        skill = next(s for s in skills if s.name == "fix-auth")
        assert "auth bug" in skill.description
        assert "read_file (path: src/auth.py)" in skill.instructions

    def test_slugifies_names(self, tmp_path):
        cap = SkillCapture()
        cap.record("list_files")
        path, _ = cap.render_skill("Weird Name!!", "desc", str(tmp_path))
        assert path.parent.name == "weird-name"

    def test_explicit_steps_override_history(self, tmp_path):
        cap = SkillCapture()
        cap.record("unrelated_tool")
        path, _ = cap.render_skill(
            "custom", "d", str(tmp_path),
            steps=[CapturedStep(tool="step_one"), CapturedStep(tool="step_two")],
        )
        body = path.read_text(encoding="utf-8")
        assert "1. step_one" in body and "2. step_two" in body

    def test_render_without_steps_raises(self, tmp_path):
        cap = SkillCapture()
        with pytest.raises(ValueError):
            cap.render_skill("empty", "d", str(tmp_path))


class TestSingleton:
    def test_get_capture_is_stable(self):
        assert get_capture() is get_capture()

    def test_reset_produces_new_instance(self):
        first = get_capture()
        first.record("x_tool")
        reset_capture()
        second = get_capture()
        assert second is not first
        assert len(second) == 0

class TestMerge:
    """Re-captures bump a count; genuinely new sequences become variants."""

    def test_identical_recapture_bumps_count_no_duplicates(self, tmp_path):
        from wisp.skill_capture import parse_captured_skill
        cap = SkillCapture()
        cap.record("list_files")
        cap.record("run_tests")
        steps = list(cap._steps)
        path1, merged1 = cap.render_skill("flow", "d", str(tmp_path), steps=steps)
        assert merged1 is False
        meta1 = parse_captured_skill(path1)
        assert meta1["captures"] == 1

        path2, merged2 = cap.render_skill("flow", "d2", str(tmp_path), steps=steps)
        assert merged2 is True
        assert path2 == path1  # same file — no sibling dirs
        meta2 = parse_captured_skill(path2)
        assert meta2["captures"] == 2
        body = path2.read_text(encoding="utf-8")
        assert body.count("## Steps") == 1
        assert "## Variants" not in body

    def test_different_sequence_appends_variant(self, tmp_path):
        from wisp.skill_capture import parse_captured_skill
        cap = SkillCapture()
        first = [CapturedStep(tool="read_file"), CapturedStep(tool="edit_file")]
        second = [CapturedStep(tool="run_tests"), CapturedStep(tool="git_commit")]
        path, _ = cap.render_skill("flow", "d", str(tmp_path), steps=first)
        path2, merged = cap.render_skill("flow", "d", str(tmp_path), steps=second)
        assert merged is True and path2 == path
        meta = parse_captured_skill(path)
        assert meta["captures"] == 2
        assert len(meta["variants"]) == 1
        assert "git_commit" in "\n".join(meta["variants"][0])

    def test_same_variant_not_duplicated(self, tmp_path):
        cap = SkillCapture()
        first = [CapturedStep(tool="a_tool")]
        second = [CapturedStep(tool="b_tool")]
        path, _ = cap.render_skill("flow", "d", str(tmp_path), steps=first)
        for _ in range(3):
            cap.render_skill("flow", "d", str(tmp_path), steps=second)
        body = path.read_text(encoding="utf-8")
        assert body.count("b_tool") == 1

    def test_foreign_skill_never_merged(self, tmp_path):
        from wisp.skill_capture import parse_captured_skill
        foreign = tmp_path / ".agents" / "skills" / "hand-made" / "SKILL.md"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("---\nname: hand-made\ndescription: human written\n---\n\nsteps here\n")
        assert parse_captured_skill(foreign) is None  # no wisp_captures marker

        foreign_body = foreign.read_text(encoding="utf-8")
        cap = SkillCapture()
        cap.record("a_tool")
        path, merged = cap.render_skill("hand-made", "d", str(tmp_path))
        assert merged is False
        assert path.parent.name == "hand-made-2"  # sibling, original untouched
        assert foreign.read_text(encoding="utf-8") == foreign_body
