#!/usr/bin/env python3
"""Integrity checker for the architecture-evolution skill system.

This is the executable guard for the skill system itself. It exists because a
convention that is only documented drifts back — the same reason the skills
themselves tell you to write tripwires.

It checks four things:

1. FRONTMATTER — every skill has a well-formed `name` matching its directory, a
   `description` free of angle brackets (the platform validator rejects those),
   and `agent_created: true` so the skill stays editable.
2. REFERENCES — every repository-relative path mentioned in a SKILL.md actually
   exists. A skill that points at a missing template is a broken skill.
3. NO LEAKAGE — the generic skills contain no project-specific identifiers. The
   case study is exempt: it is the reference document where examples belong.
4. NO ORPHANS — every template and checklist is referenced by at least one
   SKILL.md, so nothing accumulates that no skill uses.

Usage:
    python3 validate_skill_system.py [--skills-root <dir>] [--mirror <dir>] [--quiet]

`--skills-root` defaults to the skill root this script lives in, so the checker
works from either install. `--mirror` verifies a second install of the same set
is byte-identical — because two copies of one skill set is a drift hazard, and a
drift hazard that is not asserted is a drift hazard that happens.

Exit code 0 = clean, 1 = problems found. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKILLS = (
    "architecture-evolution",
    "architecture-forensics",
    "architecture-decision-engineering",
    "reliability-phase-engineering",
)

#: The reference document where concrete examples are allowed. Exempt from the
#: leakage check by design: the generic rules must stay portable, but a case
#: study that names nothing is useless.
LEAKAGE_EXEMPT = {"wisp-case-study.md"}

#: Identifiers that must not appear in the generic rules. Add your own project's
#: names here if you fork this skill system.
LEAKAGE_TERMS = (
    r"\bM1[0-9]\b", r"\bP[0-9]\b", r"\bADR-00[0-9]{2}\b",
    r"\bWisp\b", r"\bVerificationFloorGuard\b", r"\bStagnationDetector\b",
    r"\bProgressSignal\b", r"\bGOAL_[A-Z_]+\b", r"\bWISP_[A-Z_]+\b",
    r"\bOscillationTrap\b", r"\bTaskNode\b", r"\bRecoveryLadder\b",
    r"\bgraph_oscillation_guard\b", r"\bstagnation_gate\b",
    r"\brecovery_ladder\b", r"\bturn_succeeded\b", r"\bjsonschema\b",
    r"\bAgentRuntime\b", r"\bmulti_agent\b", r"\bstateless\b",
    r"\bchild_principal\b", r"\bcontext_trust\b", r"\bacceptance\.py\b",
)

FM_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
PATH_RE = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*)`")


class Report:
    def __init__(self, quiet: bool = False) -> None:
        self.problems: list[str] = []
        self.quiet = quiet

    def ok(self, msg: str) -> None:
        if not self.quiet:
            print(f"  ok    {msg}")

    def bad(self, msg: str) -> None:
        self.problems.append(msg)
        print(f"  FAIL  {msg}")


def frontmatter(text: str) -> dict[str, str] | None:
    """Parse the simple `key: value` frontmatter the platform uses.

    Deliberately regex-based rather than YAML: the platform's own validator is
    regex-based, so this reproduces its view. A skill must satisfy the gate that
    actually gates it.
    """
    m = FM_RE.match(text)
    if not m:
        return None
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.startswith((" ", "\t", "#")):
            continue
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def check_frontmatter(skill_dir: Path, rep: Report) -> None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        rep.bad(f"{skill_dir.name}: SKILL.md missing")
        return
    fm = frontmatter(skill_md.read_text())
    if fm is None:
        rep.bad(f"{skill_dir.name}: no YAML frontmatter")
        return

    name = fm.get("name", "")
    if name != skill_dir.name:
        rep.bad(f"{skill_dir.name}: name {name!r} != directory name")
    elif not re.fullmatch(r"[a-z0-9-]+", name) or "--" in name:
        rep.bad(f"{skill_dir.name}: name is not hyphen-case")
    else:
        rep.ok(f"{skill_dir.name}: name matches directory")

    desc = fm.get("description", "")
    if not desc:
        rep.bad(f"{skill_dir.name}: description missing")
    elif "<" in desc or ">" in desc:
        rep.bad(
            f"{skill_dir.name}: description contains an angle bracket — the "
            "platform validator rejects it (a folded block scalar `>` trips it)"
        )
    else:
        rep.ok(f"{skill_dir.name}: description is validator-safe")

    if fm.get("agent_created", "").lower() != "true":
        rep.bad(f"{skill_dir.name}: agent_created is not true")
    else:
        rep.ok(f"{skill_dir.name}: agent_created: true")


def check_references(skill_dirs: list[Path], rep: Report) -> None:
    """Every path-shaped token in a SKILL.md must resolve somewhere.

    Classification matters more than it looks. An earlier version skipped any
    token whose first segment was not a skill name — which silently skipped
    `assets/...` and `references/...`, i.e. every resource reference in the set.
    The check was vacuous and passed while pointing at files that did not exist.
    So classify by **shape** (a path with an extension or a trailing slash) and
    then require it to resolve; do not filter by where it starts.
    """
    for skill_dir in skill_dirs:
        text = (skill_dir / "SKILL.md").read_text()
        for token in sorted(set(PATH_RE.findall(text))):
            if not _looks_like_path(token):
                continue
            if not _resolves(token, skill_dirs):
                rep.bad(f"{skill_dir.name}: references a missing path: {token}")
        rep.ok(f"{skill_dir.name}: all path references resolve")


def _looks_like_path(token: str) -> bool:
    if "/" not in token or token.startswith(("http", "~", "/")):
        return False
    tail = token.rsplit("/", 1)[-1]
    return token.endswith("/") or "." in tail


def _resolves(token: str, skill_dirs: list[Path]) -> bool:
    for base in skill_dirs:
        if (base / token).exists():
            return True
        # a path written as `<skill>/sub/path`
        parts = token.split("/")
        if len(parts) > 1 and (base.parent / Path(*parts)).exists():
            return True
    return False


def check_leakage(skill_dirs: list[Path], rep: Report) -> None:
    for skill_dir in skill_dirs:
        text = (skill_dir / "SKILL.md").read_text()
        hits = [p for p in LEAKAGE_TERMS if re.search(p, text)]
        if hits:
            rep.bad(
                f"{skill_dir.name}: project-specific terms in the generic rules: "
                f"{hits} — move them to references/wisp-case-study.md"
            )
        else:
            rep.ok(f"{skill_dir.name}: no project-specific leakage")


def _is_generated(path: Path) -> bool:
    """True for files nobody authored: OS cruft and compiled artefacts.

    These appear in skill directories for reasons that are not decisions (a
    Finder window, a stray import), so they must not trip a check that is about
    authored content.
    """
    return (
        path.name in {".DS_Store", "Thumbs.db"}
        or path.suffix in {".pyc", ".pyo"}
        or "__pycache__" in path.parts
    )


def check_orphans(skill_dirs: list[Path], rep: Report) -> None:
    """Every template and every reference file must be referenced by a SKILL.md.

    Covers all of `assets/**` and `references/**` in every skill, not only the
    master's shared directories — a per-skill reference nobody points at is the
    same defect as an unreferenced template.
    """
    corpus = "\n".join(
        (d / "SKILL.md").read_text() for d in skill_dirs if (d / "SKILL.md").exists()
    )
    for skill_dir in skill_dirs:
        for sub in ("assets", "references"):
            directory = skill_dir / sub
            if not directory.exists():
                continue
            files = sorted(
                p for p in directory.rglob("*") if p.is_file() and not _is_generated(p)
            )
            if not files:
                continue
            orphans = [p.name for p in files if p.name not in corpus]
            if orphans:
                rep.bad(
                    f"{skill_dir.name}/{sub}: unreferenced by any SKILL.md: "
                    f"{orphans}"
                )
            else:
                rep.ok(f"{skill_dir.name}/{sub}: {len(files)} files, all referenced")


def check_mirror(source_root: Path, mirror_root: Path, rep: Report) -> None:
    """Two installs of one skill set must not drift.

    A skill set is often installed into more than one skill root, because each
    agent tool reads only its own. That is two *delivery targets*, not two
    authorities — but only for as long as the copies stay identical. Nothing
    forces them to, so this asserts it.

    Compiled artefacts (`__pycache__`, `*.pyc`) are ignored: they are generated,
    not authored, and they differ for reasons that are not drift.
    """
    if not mirror_root.is_dir():
        rep.bad(f"mirror root does not exist: {mirror_root}")
        return
    for name in SKILLS:
        src, dst = source_root / name, mirror_root / name
        if not dst.is_dir():
            rep.bad(f"{name}: missing from the mirror at {mirror_root}")
            continue
        src_files = _authored_files(src)
        dst_files = _authored_files(dst)
        if src_files != dst_files:
            rep.bad(
                f"{name}: file sets differ — "
                f"only-in-source={sorted(src_files - dst_files)} "
                f"only-in-mirror={sorted(dst_files - src_files)}"
            )
            continue
        drifted = sorted(
            f for f in src_files if (src / f).read_bytes() != (dst / f).read_bytes()
        )
        if drifted:
            rep.bad(f"{name}: drifted files: {drifted}")
        else:
            rep.ok(f"{name}: mirror identical ({len(src_files)} files)")


def _authored_files(skill_dir: Path) -> set[str]:
    return {
        str(p.relative_to(skill_dir))
        for p in skill_dir.rglob("*")
        if p.is_file() and not _is_generated(p)
    }


def main() -> int:
    here = Path(__file__).resolve()
    #: <root>/architecture-evolution/scripts/this_file  ->  <root>
    default_root = here.parents[2]

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skills-root", default=str(default_root))
    ap.add_argument("--mirror", default=None,
                    help="a second skill root to compare against, byte for byte")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = Path(args.skills_root).expanduser()
    skill_dirs = [root / s for s in SKILLS]

    print(f"validating {len(SKILLS)} skills under {root}\n")
    rep = Report(args.quiet)

    missing = [d.name for d in skill_dirs if not d.is_dir()]
    if missing:
        print(f"  FAIL  missing skill directories: {missing}")
        return 1

    print("1. frontmatter")
    for d in skill_dirs:
        check_frontmatter(d, rep)

    print("\n2. referenced paths")
    check_references(skill_dirs, rep)

    print("\n3. project-specific leakage")
    check_leakage(skill_dirs, rep)

    print("\n4. orphaned resources")
    check_orphans(skill_dirs, rep)

    if args.mirror:
        print(f"\n5. mirror drift (vs {Path(args.mirror).expanduser()})")
        check_mirror(root, Path(args.mirror).expanduser(), rep)

    print()
    if rep.problems:
        print(f"PROBLEMS: {len(rep.problems)}")
        for p in rep.problems:
            print(f"  - {p}")
        return 1
    print("SKILL SYSTEM: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
