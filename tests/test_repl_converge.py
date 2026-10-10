"""The REPL and the objective-level loop, end to end (design: docs/harness/repl-converge-design.md).

Nothing in the loop is faked except the model: a real `AgentRuntime` and engine, real tools and approvals, a real pytest probe run by the harness, the real
controller and journal, the real session dict. The scripted model decides only what it writes on each attempt, so each test fixes one fact about the
model and checks what the harness concluded.
"""

from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from wisp import autonomous_repl as AR
from wisp import coding as C
from wisp.core import convergence as CV
from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.core.session_repo import SessionRepository
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import PermissionMode, SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry
from wisp.tool_executor import ToolExecutor

BUG = "def total(xs):\n    return sum(xs) - 1\n"
FIXED = "def total(xs):\n    return sum(xs)\n"
WRONG = "def total(xs):\n    return sum(xs) + 1\n"
TEST = "from totals import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n"
OBJECTIVE = "Fix the failing tests. The bug is in totals.py."


class AttemptProvider:
    """A model that writes one file per attempt (or nothing), keyed by the attempt number the harness puts in its prompt."""

    def __init__(self, ws, writes: dict[int, tuple[str, str] | None]):
        self.ws, self.writes, self.prompts = ws, writes, []

    @staticmethod
    def attempt_of(text: str) -> int:
        for n in (5, 4, 3, 2):
            if f"--- attempt {n} ---" in text:
                return n
        return 1

    def generate_stream_events(self, system_prompt, messages, tools=None):
        user_text = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
        attempt = self.attempt_of(user_text)
        if not any(m.get("role") == "tool" for m in messages):
            self.prompts.append((attempt, user_text))
        write = self.writes.get(attempt)
        if write is not None and not any(m.get("role") == "tool" for m in messages):
            name, content = write
            yield {"type": "tool_calls", "calls": [{"id": f"w{attempt}", "type": "function", "function": {"name": "write_file", "arguments": {"path": str(self.ws / name), "content": content}}}]}
            yield {"type": "done", "done_reason": "tool_calls"}
        else:
            yield {"type": "content", "text": "done"}
            yield {"type": "done", "done_reason": "stop"}


class StubRenderer:
    def __init__(self):
        self.events: list[str] = []

    def reset(self):
        pass

    def wait_start(self, out):
        pass

    def wait_stop(self, out):
        pass

    def flush(self, out):
        pass

    def render_event(self, out, event):
        self.events.append(str(event.get("type")))


class Runner:
    """What `ReplRunner` offers the loop: the runtime, its event loop, a renderer, an output, a transport that approves, the session."""

    def __init__(self, ws, writes):
        self.ws = ws
        config = WispConfig().replace(workspace=str(ws), permission_mode=PermissionMode.ASK_ALL, max_iterations=6)
        (ws / ".wisp").mkdir(exist_ok=True)  # where the composition root keeps the store (`<workspace>/.wisp/wisp.db`)
        store = UnifiedStore(ws / ".wisp" / "repl.db")
        self.provider = AttemptProvider(ws, writes)
        executor = ToolExecutor(config)
        self.approvals: list[str] = []

        def factory():
            return WispAgentCore(config=config, provider=self.provider, security=SecurityPolicy(permission_mode=PermissionMode.ASK_ALL), tool_executor=executor)

        async def approve(event, *a, **kw):
            self.approvals.append(str(event.get("name", "")))
            return True

        self.runtime = AgentRuntime(store=store, security=SecurityPolicy(permission_mode=PermissionMode.ASK_ALL), extensions=ExtensionHost(), telemetry=Telemetry(),
                                    core_factory=factory, session_repo=SessionRepository(store), config=config)
        self.loop = asyncio.new_event_loop()
        self.out, self.renderer = io.StringIO(), StubRenderer()
        self.transport = SimpleNamespace(approve=approve)
        self.config = config
        self.session: dict = {"id": "repl", "messages": []}
        self._turn_task = None

    def close(self):
        self.loop.close()

    @property
    def printed(self) -> str:
        return self.out.getvalue()


@pytest.fixture(autouse=True)
def the_probe_runs_the_interpreter_this_test_runs(monkeypatch):
    """The harness runs the project's own command (`python -m pytest ...`) through PATH. On a machine where the first `python` is another environment, the probe cannot
    import pytest's plugins and the loop (correctly) concludes it cannot prove anything. These tests are about the loop, so they pin the interpreter; which interpreter
    a project's check should use is the open 'interpreter-level harness' item."""
    import os
    import sys

    monkeypatch.setenv("PATH", os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""))


@pytest.fixture
def project(tmp_path):
    """A small project of the usual shape: `pytest.ini` and `tests/`, which is what the host's derivation recognises as a runnable suite."""
    (tmp_path / "totals.py").write_text(BUG)
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_totals.py").write_text(TEST)
    return tmp_path


@pytest.fixture
def make_runner(project):
    made = []

    def build(writes):
        r = Runner(project, writes)
        made.append(r)
        return r

    yield build
    for r in made:
        r.close()


def journal_rows(ws) -> list[dict]:
    files = sorted(AR.journal_dir(str(ws)).glob("*.jsonl"))
    return [json.loads(line) for f in files for line in f.read_text().splitlines() if line.strip()]


class TestFixedOnTheSecondAttempt:
    @pytest.fixture
    def run(self, make_runner, project):
        runner = make_runner({1: ("totals.py", WRONG), 2: ("totals.py", FIXED)})
        result = AR.run_repl_converge(runner, OBJECTIVE, attempts=3)
        return runner, result

    def test_the_harness_says_proven_after_two_attempts_and_the_file_is_really_fixed(self, run, project):
        runner, result = run
        assert result.converged and len(result.attempts) == 2
        assert (project / "totals.py").read_text() == FIXED
        assert "✓ proven by the harness after 2 attempts" in runner.printed

    def test_progress_is_shown_as_it_happens_and_the_model_is_not_the_judge(self, run):
        runner, _ = run
        text = runner.printed
        assert text.index("▶ attempt 1/3") < text.index("attempt 1 measured") < text.index("▶ attempt 2/3") < text.index("proven")
        assert "attempt 1 measured: fail" in text.lower() or "attempt 1 measured: FAIL" in text
        assert "content" in runner.renderer.events  # the REPL's renderer saw the model's events

    def test_the_second_attempt_is_told_what_the_harness_measured(self, run):
        runner, _ = run
        second = [text for n, text in runner.provider.prompts if n == 2][0]
        assert "Acceptance conditions (measured by the harness" in second
        assert "Measured evidence for the current state" in second

    def test_the_repls_own_approval_prompt_was_used(self, run):
        runner, _ = run
        assert runner.approvals.count("write_file") == 2

    def test_the_journal_holds_both_attempts(self, run, project):
        rows = [r for r in journal_rows(project) if r.get("kind") == "attempt"]
        assert [r["index"] for r in rows] == [0, 1]
        assert rows[-1]["goal_state"] == "goal_met" and rows[0]["goal_state"] != "goal_met"

    def test_the_conversation_knows_what_happened(self, run):
        runner, _ = run
        msgs = runner.session["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == OBJECTIVE and "[loop]" in msgs[1]["content"] and "proven" in msgs[1]["content"]

    def test_attempts_did_not_run_in_the_repl_session(self, run):
        runner, _ = run
        assert runner.session["messages"][0]["content"] == OBJECTIVE and len(runner.session["messages"]) == 2


class TestNeverFixed:
    def test_the_harness_never_says_proven_and_the_bug_is_still_there(self, make_runner, project):
        runner = make_runner({})
        result = AR.run_repl_converge(runner, OBJECTIVE, attempts=3)
        assert result is not None and not result.converged
        assert (project / "totals.py").read_text() == BUG
        assert "proven by the harness" not in runner.printed
        assert "not proven" in runner.printed or "needs you" in runner.printed
        assert "not proven" in runner.session["messages"][1]["content"] or "needs you" in runner.session["messages"][1]["content"]

    def test_the_number_of_attempts_is_bounded_by_the_budget(self, make_runner):
        runner = make_runner({})
        result = AR.run_repl_converge(runner, OBJECTIVE, attempts=2)
        assert len(result.attempts) <= 2


class TestTheModelCannotPassByWeakeningTheCheck:
    def test_editing_the_test_to_pass_is_not_a_proof(self, make_runner, project):
        weakened = "from totals import total\n\n\ndef test_total():\n    assert True\n"
        runner = make_runner({1: ("tests/test_totals.py", weakened), 2: ("tests/test_totals.py", weakened), 3: ("tests/test_totals.py", weakened)})
        result = AR.run_repl_converge(runner, OBJECTIVE, attempts=3)
        assert result is not None and not result.converged, runner.printed
        assert "proven by the harness" not in runner.printed


class TestConversationAndInterruption:
    def test_the_conversation_so_far_reaches_every_attempt_as_reference(self, make_runner):
        runner = make_runner({1: ("totals.py", WRONG), 2: ("totals.py", FIXED)})
        runner.session["messages"] = [{"role": "user", "content": "we are working on the totals helper"}, {"role": "assistant", "content": "ok, the sum is off by one"}]
        AR.run_repl_converge(runner, OBJECTIVE, attempts=3)
        for attempt in (1, 2):
            text = [t for n, t in runner.provider.prompts if n == attempt][0]
            assert "[Conversation context, for reference only]" in text and "the sum is off by one" in text

    def test_an_empty_conversation_adds_no_context_block(self, make_runner):
        runner = make_runner({1: ("totals.py", FIXED)})
        AR.run_repl_converge(runner, OBJECTIVE, attempts=2)
        assert "Conversation context" not in runner.provider.prompts[0][1]

    def test_ctrl_c_stops_the_loop_between_nothing_and_everything_and_says_so(self, make_runner, project):
        import time

        runner = make_runner({})
        original = runner.provider.generate_stream_events

        def slow(system_prompt, messages, tools=None):
            time.sleep(1.5)  # an attempt that is still running when the user presses Ctrl-C
            yield from original(system_prompt, messages, tools)

        runner.provider.generate_stream_events = slow
        runner.loop.call_later(0.6, lambda: runner._turn_task.cancel())  # what the REPL's SIGINT handler does to `_turn_task`
        result = AR.run_repl_converge(runner, OBJECTIVE, attempts=3)
        assert result is None and runner._turn_task is None
        assert "■ stopped" in runner.printed and "nothing was rolled back" in runner.printed and "/converge resume" in runner.printed
        assert "stopped by the user" in runner.session["messages"][1]["content"]
        assert (project / "totals.py").read_text() == BUG


class TestTheBudgetIsBounded:
    def test_an_explicit_budget_is_clamped_to_the_maximum_and_announced(self, make_runner):
        runner = make_runner({1: ("totals.py", FIXED)})
        AR.run_repl_converge(runner, OBJECTIVE, attempts=99)
        assert f"up to {AR.MAX_ATTEMPTS} attempts" in runner.printed and "up to 99" not in runner.printed

    def test_a_zero_or_negative_budget_is_one_attempt_at_least(self, make_runner):
        runner = make_runner({1: ("totals.py", FIXED)})
        AR.run_repl_converge(runner, OBJECTIVE, attempts=-5)
        assert "up to 3 attempts" in runner.printed or "up to 1 attempts" in runner.printed


class TestRefusalsAndCommands:
    def test_an_objective_with_no_checkable_end_is_refused_not_looped(self, make_runner):
        runner = make_runner({})
        assert AR.run_repl_converge(runner, "make totals.py nicer") is None
        assert "nothing to prove" in runner.printed and runner.session["messages"] == []

    def test_the_command_without_an_argument_says_how_to_use_it(self, make_runner):
        runner = make_runner({})
        AR.handle_command(runner, "/converge")
        assert "Usage: /converge" in runner.printed

    def test_status_and_resume_with_nothing_to_resume(self, make_runner):
        runner = make_runner({})
        AR.handle_command(runner, "/converge status")
        AR.handle_command(runner, "/converge resume")
        assert "no loop has run" in runner.printed and "nothing to resume" in runner.printed

    def test_the_command_runs_the_loop(self, make_runner, project):
        runner = make_runner({1: ("totals.py", FIXED)})
        AR.handle_command(runner, f"/converge {OBJECTIVE}")
        assert (project / "totals.py").read_text() == FIXED and "✓ proven by the harness after 1 attempt:" in runner.printed

    def test_resume_continues_from_the_journal_without_rerunning_attempts(self, make_runner, project):
        first = make_runner({})
        interrupted = AR.run_repl_converge(first, OBJECTIVE, attempts=1)
        assert interrupted is not None and not interrupted.converged
        second = make_runner({2: ("totals.py", FIXED)})
        AR.handle_command(second, "/converge resume")
        rows = [r for r in journal_rows(project) if r.get("kind") == "attempt"]
        assert [r["index"] for r in rows][:1] == [0] and len(rows) >= 2
        assert [n for n, _ in second.provider.prompts][0] == 2  # the resumed run started at attempt 2, not 1


class TestRoutingFromAnOrdinaryPrompt:
    @pytest.mark.parametrize("prompt", [
        "fix the failing tests", "make the test suite pass", OBJECTIVE, "add a function named mean to totals.py",
    ])
    def test_a_stated_checkable_end_is_looped(self, project, prompt):
        assert AR.assess(prompt, str(project)).verifiable

    @pytest.mark.parametrize("prompt", [
        "explain how totals.py works", "refactor totals.py to use a loop", "rename sum to add everywhere",
        "implement the login api for this service across the module", "make totals.py nicer",
    ])
    def test_everything_else_is_not(self, project, prompt):
        a = AR.assess(prompt, str(project))
        assert not a.verifiable and a.why

    def test_the_gate_sends_a_verifiable_prompt_to_the_loop_and_a_question_to_the_model(self, make_runner, project):
        runner = make_runner({1: ("totals.py", FIXED)})
        assert C.handle_prompt(runner, OBJECTIVE) is True
        assert (project / "totals.py").read_text() == FIXED
        quiet = make_runner({})
        assert C.handle_prompt(quiet, "why are the tests in test_totals.py failing?") is False

    def test_the_kill_switch_restores_the_single_turn(self, make_runner, project, monkeypatch):
        monkeypatch.setenv("WISP_REPL_CONVERGE", "off")
        runner = make_runner({1: ("totals.py", FIXED)})
        assert C.handle_prompt(runner, OBJECTIVE) is False
        assert (project / "totals.py").read_text() == BUG

    def test_the_loop_wins_over_the_graph_when_the_end_is_checkable(self, make_runner, project):
        runner = make_runner({1: ("totals.py", FIXED)})
        prompt = "refactor the totals module across multiple files and make the tests in test_totals.py pass"
        assert C.handle_prompt(runner, prompt) is True
        assert "loop:" in runner.printed and "graph path" not in runner.printed

    @pytest.mark.parametrize("mode", ["single", "graph"])
    def test_an_explicit_single_or_graph_request_is_never_overridden(self, make_runner, project, mode):
        runner = make_runner({})
        ctx = C.task_context_from_prompt(OBJECTIVE, str(project), {"workspace": str(project)}, mode)
        assert C._loop_worthy(runner, ctx) is False

    def test_a_runner_that_cannot_run_a_loop_falls_through(self, project):
        class Bare:
            out = io.StringIO()
            session: dict = {}
            config = {"workspace": str(project)}

        assert C.handle_prompt(Bare(), OBJECTIVE) is False

    def test_the_home_directory_is_never_a_project(self, make_runner, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        runner = make_runner({})
        assert C._loop_worthy(runner, C.task_context_from_prompt(OBJECTIVE, str(tmp_path), {"workspace": str(tmp_path)})) is False


class TestWordsAndBounds:
    def test_the_attempt_budget_is_clamped(self):
        assert AR.attempts_from_env({"WISP_REPL_CONVERGE_ATTEMPTS": "99"}) == AR.MAX_ATTEMPTS
        assert AR.attempts_from_env({"WISP_REPL_CONVERGE_ATTEMPTS": "0"}) == 1
        assert AR.attempts_from_env({"WISP_REPL_CONVERGE_ATTEMPTS": "x"}) == AR.DEFAULT_ATTEMPTS
        assert AR.attempts_from_env({}) == AR.DEFAULT_ATTEMPTS

    def test_the_switch_reads_off_in_its_usual_spellings(self):
        for off in ("off", "OFF", "0", "false", "no"):
            assert not AR.routing_enabled({"WISP_REPL_CONVERGE": off})
        assert AR.routing_enabled({}) and AR.routing_enabled({"WISP_REPL_CONVERGE": "auto"})

    def test_the_conversation_context_is_bounded_and_skips_tool_chatter(self):
        session = {"messages": [{"role": "system", "content": "s"}, {"role": "tool", "content": "t"}] + [{"role": "user", "content": "u" * 2000}] * 20}
        text = AR.conversation_context(session)
        assert len(text.splitlines()) == AR.CONTEXT_MESSAGES - 0 or len(text.splitlines()) <= AR.CONTEXT_MESSAGES
        assert all(len(line) <= AR.CONTEXT_CHARS + 10 for line in text.splitlines()) and "system" not in text

    def test_proven_appears_only_for_a_met_goal(self):
        from wisp.core.goal import GoalState

        for state in GoalState:
            lines = AR.verdict_text(state, (), "reason", ("c",))
            assert ("proven" in lines[0] and "not proven" not in lines[0]) is (state is GoalState.GOAL_MET)


class TestKeepOrRevert:
    """Karpathy's rule (autoresearch `program.md`): after each attempt, keep the change if the measurement improved, otherwise put the workspace back."""

    def test_a_wrong_attempt_is_undone_and_the_next_one_starts_from_the_restored_file(self, make_runner, project):
        runner = make_runner({1: ("totals.py", WRONG), 2: ("totals.py", FIXED)})
        result = AR.run_repl_converge(runner, OBJECTIVE, attempts=3)
        assert result.converged and len(result.attempts) == 2
        assert result.attempts[0].reverted == ("totals.py",) and result.attempts[1].reverted == ()
        assert "↩ reverted" in runner.printed and "totals.py" in runner.printed
        kept = AR.latest_journal(str(project)).with_suffix(".discarded") / "attempt-1" / "totals.py"
        assert kept.read_text() == WRONG  # the attempt's own version is not lost
        second = [text for n, text in runner.provider.prompts if n == 2][0]
        assert "was reverted" in second and "Measured evidence for the current state" in second
        assert (project / "totals.py").read_text() == FIXED

    def test_when_nothing_is_ever_fixed_the_workspace_ends_as_it_began_and_the_conversation_knows(self, make_runner, project):
        runner = make_runner({1: ("totals.py", WRONG), 2: ("totals.py", WRONG + "# again\n"), 3: ("totals.py", WRONG + "# third\n")})
        result = AR.run_repl_converge(runner, OBJECTIVE, attempts=3)
        assert result is not None and not result.converged
        assert (project / "totals.py").read_text() == BUG
        assert "proven by the harness" not in runner.printed
        assert "the last attempt was reverted, so the workspace is as it was before it" in runner.printed
        assert "reverted" in runner.session["messages"][1]["content"]
        discarded = AR.latest_journal(str(project)).with_suffix(".discarded")
        assert len(list(discarded.glob("attempt-*/totals.py"))) == len(result.attempts)

    def test_the_switch_turns_it_off_and_the_wrong_file_stays(self, make_runner, project, monkeypatch):
        monkeypatch.setenv("WISP_REPL_CONVERGE_REVERT", "off")
        runner = make_runner({1: ("totals.py", WRONG)})
        result = AR.run_repl_converge(runner, OBJECTIVE, attempts=1)
        assert result.attempts[0].reverted == ()
        assert (project / "totals.py").read_text() == WRONG
        assert "keep-or-revert" not in runner.printed and "↩" not in runner.printed

    def test_the_loop_says_up_front_that_it_will_undo_attempts_that_do_not_help(self, make_runner):
        runner = make_runner({1: ("totals.py", FIXED)})
        AR.run_repl_converge(runner, OBJECTIVE, attempts=1)
        assert "keep-or-revert: an attempt that does not improve the measurement is undone" in runner.printed

    def test_a_workspace_too_big_to_snapshot_is_left_alone_and_the_loop_says_why(self, make_runner, project):
        data = project / "data"
        data.mkdir()
        for i in range(CV.SNAPSHOT_MAX_FILES + 1):  # one more than the snapshot's bound
            (data / f"{i}.txt").write_text("")
        runner = make_runner({1: ("totals.py", WRONG)})
        result = AR.run_repl_converge(runner, OBJECTIVE, attempts=1)
        assert result.attempts[0].reverted == ()
        assert f"not reverted: snapshot refused (more than {CV.SNAPSHOT_MAX_FILES} files)" in runner.printed
        assert (project / "totals.py").read_text() == WRONG

    def test_the_switch_reads_off_in_its_usual_spellings(self):
        for off in ("off", "OFF", "0", "false", "no"):
            assert not AR.revert_enabled({"WISP_REPL_CONVERGE_REVERT": off})
        assert AR.revert_enabled({}) and AR.revert_enabled({"WISP_REPL_CONVERGE_REVERT": "on"})
