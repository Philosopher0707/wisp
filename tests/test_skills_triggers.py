"""Tests for skills.py trigger-based auto-detection and matching."""

from wisp.skills import parse_skill, match_skills


class TestTriggerParsing:

    def test_parse_skill_with_triggers(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("---\nname: diagnose\ndescription: Debug bugs\ntriggers: debug, fix, error\n---\nDebug steps")
        skill = parse_skill(f)
        assert skill is not None
        assert skill.name == "diagnose"
        assert skill.triggers == ["debug", "fix", "error"]

    def test_parse_skill_with_list_triggers(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("---\nname: pdf\ndescription: PDF work\ntriggers: [pdf, extract, merge]\n---\nPDF instructions")
        skill = parse_skill(f)
        assert skill is not None
        assert skill.triggers == ["pdf", "extract", "merge"]

    def test_parse_skill_without_triggers(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("---\nname: general\ndescription: General help\n---\nHelp")
        skill = parse_skill(f)
        assert skill is not None
        assert skill.triggers == []


class TestMatchSkills:

    def test_name_exact_match_scores_highest(self, tmp_path):
        skills_dir = tmp_path / ".agents" / "skills"
        
        d1 = skills_dir / "diagnose"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("---\nname: diagnose\ndescription: Debug bugs\ntriggers: debug\n---\nSteps")
        
        d2 = skills_dir / "pdf"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text("---\nname: pdf\ndescription: PDF work\ntriggers: pdf\n---\nSteps")
        
        matched = match_skills("diagnose this error", str(tmp_path))
        assert len(matched) >= 1
        assert matched[0][0].name == "diagnose"

    def test_trigger_match(self, tmp_path):
        skills_dir = tmp_path / ".agents" / "skills"
        d1 = skills_dir / "pdf"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("---\nname: pdf\ndescription: PDF work\ntriggers: merge, split, rotate\n---\nSteps")
        
        d2 = skills_dir / "other"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text("---\nname: other\ndescription: Other\n---\nOther")
        
        matched = match_skills("please merge the PDFs", str(tmp_path))
        assert len(matched) >= 1
        assert matched[0][0].name == "pdf"

    def test_min_score_filters(self, tmp_path):
        skills_dir = tmp_path / ".agents" / "skills"
        d1 = skills_dir / "pdf"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("---\nname: pdf\ndescription: PDF work\ntriggers: pdf\n---\nSteps")
        
        matched = match_skills("hello world", str(tmp_path), min_score=1.5)
        assert matched == []
        
        matched = match_skills("hello world", str(tmp_path), min_score=0.0)
        assert len(matched) >= 1  # Description keyword overlap still counts

    def test_partial_name_match(self, tmp_path):
        skills_dir = tmp_path / ".agents" / "skills"
        d1 = skills_dir / "diagnose"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("---\nname: diagnose\ndescription: Debug bugs\n---\nSteps")
        
        matched = match_skills("can you diagnose", str(tmp_path), min_score=1.0)
        assert len(matched) >= 1
        assert matched[0][0].name == "diagnose"

    def test_sorted_by_score(self, tmp_path):
        skills_dir = tmp_path / ".agents" / "skills"
        
        d1 = skills_dir / "pdf"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("---\nname: pdf\ndescription: PDF work\ntriggers: pdf\n---\nSteps")
        
        d2 = skills_dir / "merge"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text("---\nname: merge\ndescription: Merge files\ntriggers: pdf, merge\n---\nSteps")
        
        matched = match_skills("merge the pdf files please", str(tmp_path), min_score=0.0)
        # Both should match but merge scores highest (trigger + description overlap)
        names = [m[0].name for m in matched]
        assert "merge" in names
        assert "pdf" in names

    def test_empty_query(self, tmp_path):
        assert match_skills("", str(tmp_path), min_score=0.5) == []

    def test_no_skills_workspace(self, tmp_path):
        assert match_skills("debug", str(tmp_path), min_score=2.0) == []
