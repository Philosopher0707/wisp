"""Layer 5. INVARIANT V1: a command is verification only if a recognised tool's exit status decides the whole command's result."""

from __future__ import annotations

import pytest

from wisp.core.gates.verify import classify
from wisp.core.verification import COMMAND_ARG, VerificationFloorGuard

EVIDENCE = [
    "pytest", "pytest -q tests/", "python -m pytest -x", "python3 -m pytest", "py.test", "tox", "nox -s tests", "python -m unittest discover", "uv run pytest", "poetry run pytest", "pipenv run pytest", "env CI=1 pytest",
    "timeout 300 pytest", "nohup pytest", "time pytest", "cd sub && pytest -x", "cd sub && pytest && echo ok", "pytest && echo passed", "pytest || exit 1", "pytest || false", "pytest -q 2>&1", "pytest > out.log 2>&1",
    "(cd sub && pytest)", "{ pytest; }", "set -o pipefail; pytest | tail -5", "set -euo pipefail; pytest 2>&1 | tee out.log", "bash -o pipefail -c 'pytest | tail'", "bash -eo pipefail -c 'pytest | tail'",
    "bash -c pytest", "bash -lc 'pytest -q'", "sh -c 'cd x && pytest'", "ruff check .", "ruff format --check .", "ruff format --diff", "mypy wisp", "python -m mypy wisp", "pyright", "pylint wisp", "flake8", "black --check .",
    "isort --check .", "eslint .", "tsc --noEmit", "npx tsc", "npx eslint src", "prettier --check .", "jest", "npx vitest run", "mocha", "npm test", "npm run test", "npm run lint", "npm run typecheck", "npm run build",
    "npm run test:unit", "yarn test", "yarn lint", "pnpm test", "pnpm run build", "pnpm exec vitest", "bun test", "cargo test", "cargo check", "cargo clippy", "cargo build", "cargo fmt --check", "go test ./...",
    "go vet ./...", "go build ./...", "swift test", "swift build", "gradle test", "./gradlew check", "mvn test", "./mvnw verify", "dotnet test", "dotnet build", "deno test", "deno lint", "make test", "make check",
    "make lint", "make", "make ci", "make test-unit", "rake test", "bundle exec rspec", "rspec", "phpunit", "ctest", "python -m compileall -q .", "shellcheck x.sh",
]
PROVES_NOTHING = [
    # always exits 0, or prints a word
    "true", ":", "echo ok", "echo pytest", "echo 'all tests passed'", "printf pytest", "cat pytest.ini", "ls tests", "grep pytest requirements.txt", "which pytest", "pytest --version", "cd x && echo pytest",
    "python script.py", "python -c 'print(1)'", "node app.js", "./run.sh", "make clean", "npm install", "pip install pytest", "git status", "sleep 1",
    # the runner's failure is swallowed
    "pytest || true", "pytest || :", "pytest || echo failed", "pytest || echo failed && true", "pytest; true", "pytest; echo done", "pytest\necho done", "pytest && true || true", "! pytest", "pytest &",
    "pytest | tail", "pytest | tail -5", "pytest 2>&1 | tee out.log", "pytest | head -20", "pytest | wc -l", "pytest | grep -c passed", "(pytest; true)", "{ pytest || true; }", "bash -c 'pytest || true'",
    "bash -c 'pytest | tail'", "set +o pipefail; pytest | tail", "pytest | tail && echo done",
    # runs the tool without running the checks
    "pytest --collect-only", "pytest --co -q", "pytest --help", "pytest -h", "ruff --help", "mypy --version", "ruff format .", "black .", "isort .", "prettier --write .", "make -n test", "make --dry-run", "make -q",
    "tsc --init", "cargo fmt", "eslint --help", "npm run start", "npm run dev", "npm run clean", "npx some-formatter", "xargs pytest", "find . -name test_x.py | xargs pytest",
    # not analysable, so not trusted
    "", "   ", "$CMD", "eval pytest", "pytest 'unterminated", "bash -c \"$X\"",
]


@pytest.mark.parametrize("command", EVIDENCE)
def test_real_verification_is_recognised(command):
    v = classify(command)
    assert v.ok, (command, v.reason)
    assert v.runner and v.kind in ("test", "lint", "typecheck", "build")


@pytest.mark.parametrize("command", PROVES_NOTHING)
def test_a_command_that_can_exit_zero_on_broken_code_is_not_verification(command):
    assert not classify(command).ok, command


def test_classification_is_a_function_of_the_text_alone():
    for c in EVIDENCE + PROVES_NOTHING:
        assert classify(c) == classify(c)


def test_the_kind_distinguishes_test_lint_typecheck_and_build():
    assert classify("pytest").kind == "test"
    assert classify("ruff check .").kind == "lint"
    assert classify("mypy wisp").kind == "typecheck"
    assert classify("cargo build").kind == "build"


class TestTheGuardUsesIt:
    """The completion gate itself: a turn that changed code may not finish on a command that proves nothing."""

    def _edited(self) -> VerificationFloorGuard:
        g = VerificationFloorGuard()
        g.note_tool_result("write_file", "wrote", {"path": "a.py"})
        return g

    def _ran(self, g: VerificationFloorGuard, command: str, output: str = "ok") -> None:
        g.note_tool_result("run_bash", output, {COMMAND_ARG: command, "command": command[:40]})

    @pytest.mark.parametrize("command", ["true", "echo ok", "pytest || true", "pytest | tail", "pytest; echo done", "python script.py", "make clean"])
    def test_a_passing_command_that_proves_nothing_does_not_verify(self, command):
        g = self._edited()
        self._ran(g, command)
        assert g.verify_ok_after_edit is None and g.resolved() is False
        assert g.rejection() is not None

    @pytest.mark.parametrize("command", ["pytest -q", "cd sub && pytest", "ruff check .", "npm test", "set -o pipefail; pytest | tail"])
    def test_a_passing_real_verifier_verifies(self, command):
        g = self._edited()
        self._ran(g, command)
        assert g.verify_ok_after_edit is True and g.resolved() is True and g.rejection() is None

    @pytest.mark.parametrize("command", ["pytest -q", "true", "echo ok"])
    def test_a_failing_command_is_always_recorded_as_a_failure(self, command):
        g = self._edited()
        self._ran(g, command, output="[exit code: 1]\nboom")
        assert g.verify_ok_after_edit is False

    def test_a_proves_nothing_command_after_a_real_pass_does_not_erase_it(self):
        g = self._edited()
        self._ran(g, "pytest -q")
        self._ran(g, "echo done")
        assert g.verify_ok_after_edit is True

    def test_a_later_edit_still_invalidates_earlier_evidence(self):
        g = self._edited()
        self._ran(g, "pytest -q")
        g.note_tool_result("edit_file", "edited", {"path": "a.py"})
        assert g.verify_ok_after_edit is None

    def test_without_the_command_the_legacy_behaviour_is_kept(self):
        g = self._edited()
        g.note_tool_result("run_bash", "ok", {"command": "echo ok"})
        assert g.verify_ok_after_edit is True  # callers that cannot supply the command (tests, legacy) are unchanged

    def test_the_command_does_not_leak_into_the_step_trail(self):
        g = self._edited()
        self._ran(g, "pytest -q")
        assert all(COMMAND_ARG not in args for _name, args in g.steps)
