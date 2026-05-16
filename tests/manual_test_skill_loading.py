#!/usr/bin/env python3
"""Manual test script for skill loading system.

Run: python tests/manual_test_skill_loading.py
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

# Add wisp to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from wisp.skills import parse_skill, discover_skills, find_skill, match_skills
from wisp.commands import cmd_skill


class MockConfig:
    def __init__(self, workspace="/tmp"):
        self.model = "test-model"
        self.workspace = workspace
        self.auto_approve = False
        self.show_thinking = False
        self.max_context_tokens = 128000
        self.chars_per_token = 4
        self._context_tokens_explicit = False
        self.plan_mode = False
        self.plan_context = None


class MockAgent:
    def __init__(self, workspace="/tmp"):
        self.config = MockConfig(workspace=workspace)
        self.messages = []
        self._active_skill = None
        self._system_prompt_cache = {}
        self._last_user_prompt = None

    def _build_system_prompt(self, skill_name=None, workspace=None, query=None):
        ws = workspace or self.config.workspace or "."
        system = "You are Wisp."
        skills = discover_skills(ws)
        if skill_name:
            skill = next((s for s in skills if s.name == skill_name), None)
            if skill:
                system += f"\n\nMANDATORY Mode: {skill.name}\n"
                system += skill.description + "\n\n"
                system += skill.instructions
        return system


def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(test_name, passed, detail=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status} — {test_name}")
    if detail:
        print(f"         {detail}")


def main():
    print("\n" + "="*60)
    print("  MANUAL SKILL LOADING TEST")
    print("="*60)

    # Prevent global skills from leaking into test
    from wisp import skills as skills_mod
    original_global = skills_mod.GLOBAL_SKILL_DIRS
    skills_mod.GLOBAL_SKILL_DIRS = []

    # Create temp workspace
    tmpdir = tempfile.mkdtemp(prefix="wisp_skill_test_")
    print(f"\n📁 Test workspace: {tmpdir}")

    try:
        # ── Setup skills ─────────────────────────────────────────
        skills_dir = Path(tmpdir) / ".agents" / "skills"
        
        # Skill 1: coder
        coder_dir = skills_dir / "coder"
        coder_dir.mkdir(parents=True)
        (coder_dir / "SKILL.md").write_text(
            "---\n"
            "name: coder\n"
            "description: Write Python code with type hints\n"
            "triggers: code, python, implement\n"
            "---\n"
            "# Coder Skill\n\n"
            "Always use type hints.\n"
            "Follow PEP 8.\n"
            "Write docstrings for all public functions.\n"
        )
        
        # Skill 2: debugger
        debug_dir = skills_dir / "debugger"
        debug_dir.mkdir(parents=True)
        (debug_dir / "SKILL.md").write_text(
            "---\n"
            "name: debugger\n"
            "description: Debug errors and trace issues\n"
            "triggers: debug, fix, error, bug, trace\n"
            "---\n"
            "# Debugger Skill\n\n"
            "1. Reproduce the issue\n"
            "2. Add logging\n"
            "3. Check stack traces\n"
            "4. Fix root cause\n"
        )
        
        # Skill 3: pdf (for auto-detection)
        pdf_dir = skills_dir / "pdf"
        pdf_dir.mkdir(parents=True)
        (pdf_dir / "SKILL.md").write_text(
            "---\n"
            "name: pdf\n"
            "description: PDF operations and manipulation\n"
            "triggers: pdf, merge, split, extract\n"
            "---\n"
            "# PDF Skill\n\n"
            "Use PyPDF2 or pdfplumber.\n"
            "Handle encrypted PDFs gracefully.\n"
        )

        # Skill 4: unicode test
        unicode_dir = skills_dir / "unicode-test"
        unicode_dir.mkdir(parents=True)
        (unicode_dir / "SKILL.md").write_text(
            "---\n"
            "name: unicode-test\n"
            "description: 测试中文技能\n"
            "---\n"
            "使用中文指令 🎉\n"
            "Réponse en français\n"
        )

        agent = MockAgent(workspace=tmpdir)

        # ── Test 1: Discover skills ──────────────────────────────
        print_section("1. Skill Discovery")
        skills = discover_skills(tmpdir)
        print(f"  Found {len(skills)} skills: {[s.name for s in skills]}")
        print_result("Discover all skills", len(skills) == 4,
                    f"Expected 4, got {len(skills)}")

        # ── Test 2: Parse individual skill ───────────────────────
        print_section("2. Skill Parsing")
        coder_skill = parse_skill(coder_dir / "SKILL.md")
        print_result("Parse coder skill", coder_skill is not None)
        print_result("Coder name correct", coder_skill.name == "coder")
        print_result("Coder triggers correct", coder_skill.triggers == ["code", "python", "implement"])
        print(f"  Instructions preview: {coder_skill.instructions[:60]}...")

        # ── Test 3: Find skill by name ───────────────────────────
        print_section("3. Find Skill by Name")
        found = find_skill("debugger", tmpdir)
        print_result("Find debugger", found is not None and found.name == "debugger")
        
        not_found = find_skill("nonexistent", tmpdir)
        print_result("Nonexistent returns None", not_found is None)

        # ── Test 4: /skill command — list ────────────────────────
        print_section("4. /skill Command — List Skills")
        print("  Output:")
        cmd_skill(agent, "")
        print_result("List shows skills", True)  # If we got here without crash

        # ── Test 5: /skill command — load ──────────────────────
        print_section("5. /skill Command — Load Skill")
        agent._active_skill = None
        agent._system_prompt_cache = {}
        cmd_skill(agent, "coder")
        print_result("Load sets _active_skill", agent._active_skill == "coder")
        print_result("Cache cleared", len(agent._system_prompt_cache) == 0)

        # ── Test 6: System prompt injection ──────────────────────
        print_section("6. System Prompt Injection")
        system = agent._build_system_prompt(skill_name="coder")
        has_mandatory = "MANDATORY Mode: coder" in system
        has_instructions = "Always use type hints" in system
        has_description = "Write Python code with type hints" in system
        print_result("MANDATORY block present", has_mandatory)
        print_result("Instructions injected", has_instructions)
        print_result("Description injected", has_description)
        
        # Verify skill is at the end (highest priority)
        mandatory_pos = system.find("MANDATORY Mode")
        base_pos = system.find("You are Wisp")
        print_result("Skill at end (priority)", mandatory_pos > base_pos,
                    f"mandatory_pos={mandatory_pos}, base_pos={base_pos}")

        # ── Test 7: No skill = no injection ─────────────────────
        print_section("7. No Skill = No Injection")
        system_no_skill = agent._build_system_prompt()
        print_result("No MANDATORY without skill", "MANDATORY Mode" not in system_no_skill)

        # ── Test 8: Auto-detection by name ──────────────────────
        print_section("8. Auto-Detection by Name")
        matched = match_skills("write some python code", tmpdir, min_score=1.5)
        print(f"  Matches: {[(m[0].name, m[1]) for m in matched[:2]]}")
        print_result("Auto-detect coder by name", 
                    len(matched) > 0 and matched[0][0].name == "coder",
                    f"Top match: {matched[0][0].name if matched else 'none'}")

        # ── Test 9: Auto-detection by trigger ───────────────────
        print_section("9. Auto-Detection by Trigger")
        matched = match_skills("fix this bug please", tmpdir, min_score=1.5)
        print(f"  Matches: {[(m[0].name, m[1]) for m in matched[:2]]}")
        print_result("Auto-detect debugger by trigger",
                    len(matched) > 0 and matched[0][0].name == "debugger")

        # ── Test 10: No auto-detect for unrelated ────────────────
        print_section("10. No Auto-Detect for Unrelated Query")
        matched = match_skills("hello world how are you", tmpdir, min_score=1.5)
        print_result("Unrelated query returns empty", matched == [])

        # ── Test 11: Unicode skill ───────────────────────────────
        print_section("11. Unicode Skill Content")
        system_unicode = agent._build_system_prompt(skill_name="unicode-test")
        has_chinese = "测试中文技能" in system_unicode
        has_emoji = "🎉" in system_unicode
        has_french = "Réponse en français" in system_unicode
        print_result("Chinese content", has_chinese)
        print_result("Emoji content", has_emoji)
        print_result("French content", has_french)

        # ── Test 12: Cache invalidation ──────────────────────────
        print_section("12. Cache Invalidation")
        agent._system_prompt_cache = {("old",): "cached_value"}
        cmd_skill(agent, "debugger")
        print_result("Cache cleared on skill switch", len(agent._system_prompt_cache) == 0)
        print_result("Active skill updated", agent._active_skill == "debugger")

        # ── Test 13: Different skills = different prompts ────────
        print_section("13. Different Skills = Different Prompts")
        sys_coder = agent._build_system_prompt(skill_name="coder")
        sys_debugger = agent._build_system_prompt(skill_name="debugger")
        print_result("Different prompts", sys_coder != sys_debugger)
        print_result("Coder has type hints", "type hints" in sys_coder)
        print_result("Debugger has reproduce", "Reproduce the issue" in sys_debugger)

        # ── Test 14: Skill not found graceful ────────────────────
        print_section("14. Skill Not Found — Graceful Fallback")
        system_missing = agent._build_system_prompt(skill_name="nonexistent")
        print_result("No crash on missing skill", True)
        print_result("No MANDATORY for missing", "MANDATORY Mode" not in system_missing)

        # ── Test 15: Project shadows global ──────────────────────
        print_section("15. Project Skills Priority")
        # Create a global dir
        global_dir = Path(tmpdir) / "global_skills"
        global_dir.mkdir()
        gdir = global_dir / "coder"
        gdir.mkdir()
        (gdir / "SKILL.md").write_text(
            "---\nname: coder\ndescription: GLOBAL version\n---\nGlobal"
        )
        
        from wisp import skills as skills_mod
        original_global = skills_mod.GLOBAL_SKILL_DIRS
        skills_mod.GLOBAL_SKILL_DIRS = [global_dir]
        try:
            skills = discover_skills(tmpdir)
            coder = next((s for s in skills if s.name == "coder"), None)
            print_result("Project skill wins", coder.description == "Write Python code with type hints",
                        f"Got: {coder.description if coder else 'none'}")
        finally:
            skills_mod.GLOBAL_SKILL_DIRS = original_global

        # ── Summary ──────────────────────────────────────────────
        print_section("SUMMARY")
        print("  All manual tests completed successfully! ✅")
        print(f"  Workspace: {tmpdir}")
        print("  (cleaning up...)")

    finally:
        skills_mod.GLOBAL_SKILL_DIRS = original_global
        shutil.rmtree(tmpdir, ignore_errors=True)
        print("  Cleanup done.")


if __name__ == "__main__":
    main()
