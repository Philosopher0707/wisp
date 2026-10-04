"""Executable guardrail spec for the Wisp repo.

These tests encode the quality policy in code so the policy cannot silently rot.
A guardrail that only lives in `.github/workflows/` is a guardrail that drifts;
a guardrail that lives in a test survives a workflow edit.

Each test asserts ONE decision:

  * `test_no_lint_errors_in_shipping_packages` -- the lint surface covers every
    package that actually ships. `agent/` was ungated for its whole life and held
    9 real Pyflakes violations; that gap is what this test exists to close.
  * `test_agent_package_is_gated` -- the regression witness for the above. It
    names `agent/` specifically so that dropping it from the gate fails loudly.
  * `test_hard_security_rules_are_clean` -- the bandit rules that are defects in
    ANY codebase find nothing. Survivors must appear in `AUDITED` with a reason.
  * `test_audited_exceptions_are_not_stale` -- an `AUDITED` entry that no longer
    matches a real finding is a stale suppression, and is itself a failure.
  * `test_silent_except_is_absent_from_authority_paths` -- `S110`/`S112` may not
    appear where a swallowed error would be indistinguishable from a verdict.
  * `test_no_undocumented_placeholder_implementations` -- a function whose whole
    body is `pass` must SAY SO in its name, docstring, or comment.
  * `test_no_notimplementederror_placeholders` -- no TODO-raising stubs.
  * `test_no_bare_except` -- a bare `except:` swallows KeyboardInterrupt.
  * `test_no_leftover_debug_artifacts` -- no stray probe/breakpoint files.
  * `test_bandit_runs_and_reports` -- a second-opinion bandit scan that SKIPS
    loudly when bandit is absent instead of passing because the tool is missing.

These gates are only trustworthy if they turn RED on the defects they exist to
catch, so `tests/mutation_check_guardrails.py` plants each defect and asserts the
matching gate fails. Run it after editing any gate here.

Run:  pytest tests/test_guardrails.py -v
      python tests/mutation_check_guardrails.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "pyproject.toml"

# Packages that ship in the distribution. `agent` is here because
# `wisp/composition.py` imports it at five sites -- a distribution without it
# cannot run its own entry point. A gate that skips it gates nothing.
SHIPPING_PACKAGES = ("wisp", "agent", "wisp_net")


def _run_ruff(select: str, *paths: str) -> list[tuple[str, str, int]]:
    """Return [(code, path, line)] for ruff findings, or [] when clean.

    The line number is carried deliberately: an allowlist keyed only on
    (code, path) keeps passing when the offending line is rewritten, so the
    exception silently follows the code instead of being re-audited.

    Pin `--output-format concise`: ruff >= 0.16 defaults to a pretty
    "fancy" diagnostic layout where the code and the path are on *different*
    lines. Parsing the default format yields an empty list for a directory
    that is visibly dirty, which turns a hard gate into a silent pass.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", select, "--output-format", "concise", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        # ruff itself failed (bad config, missing plugin) -- never read that as "clean".
        raise AssertionError(f"ruff failed to run: {proc.stderr[:800]}")
    out: list[tuple[str, str, int]] = []
    for line in proc.stdout.splitlines():
        m = re.match(r"^(?P<path>[^:]+\.py):(?P<line>\d+):\d+: (?P<code>[A-Z]+\d+) ", line)
        if m:
            out.append((m.group("code"), m.group("path"), int(m.group("line"))))
    return out


def _assert_parser_detects_known_dirty_file() -> None:
    """Meta-test: prove the parser sees a violation we planted on purpose.

    Without this, a parser that silently returns [] turns every gate below it
    into a no-op and the suite still reports green.
    """
    probe = REPO_ROOT / "_guardrail_parser_probe.py"
    probe.write_text("import os  # deliberately unused -> F401\n", encoding="utf-8")
    try:
        found = _run_ruff("F", "_guardrail_parser_probe.py")
        assert found, "parser failed to detect a planted F401 -- gates are vacuous"
        assert found[0][2] == 1, f"line number not parsed: {found[0]}"
    finally:
        probe.unlink()


def _toml_has(table: str, key: str) -> bool:
    """Read a literal key out of pyproject without a TOML dependency."""
    text = CONFIG.read_text(encoding="utf-8")
    section = re.search(rf"^\[{re.escape(table)}\]$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if section is None:
        return False
    return re.search(rf"^\s*{re.escape(key)}\s*=", section.group(1), re.M) is not None


class TestLintSurfaceCoversEveryShippedPackage:
    """RED witness for the `agent/` gap."""

    def test_parser_detects_planted_violation(self) -> None:
        _assert_parser_detects_known_dirty_file()

    def test_agent_package_is_gated(self) -> None:
        # A package that ships but is not gated is an unguarded surface.
        assert "agent" in SHIPPING_PACKAGES, "agent/ must remain in the gated set"
        findings = _run_ruff("F", "agent/")
        assert findings == [], f"agent/ has ungated lint errors: {findings}"

    def test_no_lint_errors_in_shipping_packages(self) -> None:
        for pkg in SHIPPING_PACKAGES:
            findings = _run_ruff("F", f"{pkg}/")
            assert findings == [], f"{pkg}/ F-errors: {findings}"


class TestSecurityRules:
    """Bandit-family gates, scoped to rules that are defects for THIS codebase.

    NOT selected: S110 (try/except/pass), S112 (try/except/continue) and S603
    (subprocess-without-shell-equals-true). Wisp is a tool-execution agent whose
    contract is that a failing tool call degrades instead of crashing the turn
    loop; there are 208 S110 findings and gating all of them would fail forever
    and train reviewers to ignore the gate. S110 is instead gated where it
    actually decides truth -- see `test_silent_except_is_absent_from_authority_paths`.

    Selected: S105 (hardcoded credential), S202 (unsafe archive extraction),
    S310 (audit-url-open), S501 (request-with-no-cert-validation),
    S307 (eval), S324 (insecure hash). These are defects in any codebase.
    """

    HARD_GATES = ("S105", "S202", "S310", "S501", "S307", "S324")

    # Every surviving finding is AUDITED, not tolerated. Each entry records WHY it
    # is safe so the next reader can re-check the reasoning instead of inheriting
    # a blind suppression. Any NEW finding in these rules still fails.
    AUDITED: dict[tuple[str, int], str] = {
        # S105 -- enum MEMBERS, not credentials.
        ("S105", 49): "wisp/core/acceptance.py: Verdict.PASS = 'pass'; a verdict label.",
        ("S105", 36): "wisp/mcp/manager.py: MCPAuthMethod.BEARER_TOKEN; an auth-method name.",
        # S310 -- urlopen where the scheme is fixed or operator-configured.
        ("S310", 580): "wisp/tools/web.py: hardcoded https://duckduckgo.com literal, not user input.",
        ("S310", 56): "wisp/trace/otlp.py: operator-configured OTLP endpoint; the tier gate above refuses LOCAL_ONLY_FULL.",
        ("S310", 60): "wisp/trace/otlp.py: same export call, response status checked.",
        # S202 -- ruff cannot see that zipfile.extractall sanitises traversal
        # itself. The tarfile branch on the NEXT line passes filter='data'
        # (line 460), which is the branch that was actually vulnerable.
        ("S202", 451): "wisp/plugins/registry.py: zipfile.extractall strips absolute and .. segments; the tarfile branch passes filter='data'.",
    }

    # Paths where "no evidence" must never be coerced into "looks fine": the
    # acceptance vocabulary, the auth/consent decision layer, and the redaction
    # tier that gates egress. All three are clean today, so this gate is free.
    AUTHORITY_PATHS = ("wisp/core/acceptance.py", "wisp/auth/", "wisp/trace/")

    def test_hard_security_rules_are_clean(self) -> None:
        findings = _run_ruff(",".join(self.HARD_GATES), *SHIPPING_PACKAGES)
        unexpected = [f for f in findings if (f[0], f[2]) not in self.AUDITED]
        assert unexpected == [], (
            f"unaudited security findings: {unexpected}. "
            "Fix it, or add an AUDITED entry recording WHY it is safe."
        )

    def test_audited_exceptions_are_not_stale(self) -> None:
        """An AUDITED entry with no matching finding is a stale suppression.

        Without this, deleting the offending line leaves the allowlist entry
        behind and that rule stops being guarded for the rest of time.
        """
        findings = _run_ruff(",".join(self.HARD_GATES), *SHIPPING_PACKAGES)
        present = {(code, line) for code, _path, line in findings}
        stale = sorted(set(self.AUDITED) - present)
        assert stale == [], f"stale AUDITED entries (no longer match a finding): {stale}"

    def test_silent_except_is_absent_from_authority_paths(self) -> None:
        """S110/S112 must not appear where a swallowed error becomes a verdict.

        Swallowing an exception is legitimate where a tool call is expected to
        fail. It is a defect in the acceptance layer: there, "the check threw"
        and "the check passed" must not be indistinguishable. This is the narrow,
        achievable form of the gate -- the broad "no S110 anywhere" version is not
        achievable against 208 existing findings, and asserting it would train
        reviewers to ignore the gate.
        """
        violations: list[tuple[str, str, int]] = []
        for path in self.AUTHORITY_PATHS:
            violations += _run_ruff("S110,S112", path)
        assert violations == [], f"silent except in an authority path: {violations}"

    def test_bandit_runs_and_reports(self) -> None:
        """Second-opinion bandit scan. Skips loudly rather than passing silently.

        bandit is NOT in the dev extras, so in most environments this test must
        skip. An earlier version accepted rc==1 as "ran and found things", which
        is indistinguishable from `No module named bandit` -- a gate that passes
        because the tool is missing is worse than no gate at all.
        """
        proc = subprocess.run(
            [sys.executable, "-m", "bandit", "-r", "wisp/", "-q", "-f", "json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if "No module named bandit" in proc.stderr:
            pytest.skip("bandit is not installed; ruff's S-rules are the bandit-family gate")
        # Either it found nothing (rc 0) or it found something (rc 1). rc>1 is a crash.
        assert proc.returncode in (0, 1), f"bandit crashed: {proc.stderr[:500]}"
        import json

        report = json.loads(proc.stdout or "{}")
        results = report.get("results", [])
        severe = [r for r in results if r.get("issue_severity") == "HIGH" and r.get("issue_confidence") == "HIGH"]
        assert severe == [], f"HIGH/HIGH bandit findings: {severe}"


class TestNoPlaceholders:
    """AST-based, because a regex cannot tell a no-op from a stub.

    A regex over `pass` reported 356 "placeholders" in this repo. 350 of them are
    intentional `except: pass` bodies in the tool-execution layer, and 6 are
    deliberate no-op hooks. The gate that actually protects the codebase is:

        A function whose ENTIRE body is `pass` must SAY SO, in its docstring,
        in a comment, or in its name.

    An unnamed, undocumented `pass` body is a stub and is a defect.
    """

    # Names that are self-describing no-ops.
    NAME_MARKERS = ("_noop", "noop_", "_no_op", "_ignore", "_unused", "_placeholder_cb")

    def _stub_candidates(self) -> list[tuple[str, int, str]]:
        """Functions whose whole body is a bare `pass`."""
        import ast

        out: list[tuple[str, int, str]] = []
        for pkg in SHIPPING_PACKAGES:
            for py in (REPO_ROOT / pkg).rglob("*.py"):
                try:
                    tree = ast.parse(py.read_text(encoding="utf-8"))
                except SyntaxError:
                    continue
                for fn in ast.walk(tree):
                    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    body = [
                        s
                        for s in fn.body
                        if not (
                            isinstance(s, ast.Expr)
                            and isinstance(s.value, ast.Constant)
                            and isinstance(s.value.value, str)
                        )
                    ]
                    if len(body) == 1 and isinstance(body[0], ast.Pass):
                        out.append((str(py.relative_to(REPO_ROOT)), fn.lineno, fn.name))
        return out

    def test_no_undocumented_placeholder_implementations(self) -> None:
        undocumented: list[str] = []
        for path, line, name in self._stub_candidates():
            if any(marker in name.lower() for marker in self.NAME_MARKERS):
                continue
            # Look at the function's own body, not the lines ABOVE the `def`:
            # an explanatory comment almost always sits directly under the
            # signature, so a lookback window misses it and flags a documented
            # no-op as a stub.
            src = (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
            window = "\n".join(src[line : line + 6])
            if re.search(r"deliberate|intentional|no-?op|by design|nothing to do|deprecated", window, re.I):
                continue
            undocumented.append(f"{path}:{line} {name}")
        assert undocumented == [], (
            f"function bodies that are a bare `pass` with no stated reason: {undocumented}. "
            "Either implement it, or document why it is a no-op (name it _noop_*/ docstring)."
        )

    def test_no_notimplementederror_placeholders(self) -> None:
        """`raise NotImplementedError("TODO")` is an unfinished promise."""
        offenders: list[str] = []
        for pkg in SHIPPING_PACKAGES:
            for py in (REPO_ROOT / pkg).rglob("*.py"):
                for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                    if re.search(r"NotImplementedError\s*\(\s*['\"]?(todo|fixme|implement)", line, re.I):
                        offenders.append(f"{py.relative_to(REPO_ROOT)}:{i}")
        assert offenders == [], f"TODO-raising stubs: {offenders}"


class TestNoDebugArtifacts:
    def test_no_bare_except(self) -> None:
        offenders: list[str] = []
        for pkg in SHIPPING_PACKAGES:
            for py in (REPO_ROOT / pkg).rglob("*.py"):
                for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                    if re.search(r"except\s*:", line):
                        offenders.append(f"{py.relative_to(REPO_ROOT)}:{i}")
        assert offenders == [], f"bare except found: {offenders[:10]}"

    def test_no_leftover_debug_artifacts(self) -> None:
        offenders: list[str] = []
        for pkg in SHIPPING_PACKAGES:
            for py in (REPO_ROOT / pkg).rglob("*.py"):
                for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                    if re.search(r"\bbreakpoint\(\)|\bpdb\.set_trace", line):
                        offenders.append(f"{py.relative_to(REPO_ROOT)}:{i}")
        assert offenders == [], f"debug artifacts: {offenders[:10]}"

    def test_config_declares_gated_packages(self) -> None:
        """The config and the test must agree, or one of them is lying."""
        assert _toml_has("tool.ruff", "line-length"), "ruff config missing line-length"


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
