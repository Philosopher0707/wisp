"""GH#21: Terminal-Bench adapter pins.

PTY backend runs for real (local, hermetic); Docker backend runs against
an injected fake (no daemon here). Mock providers script the model.
"""

from __future__ import annotations

import json

from wisp.benchmark.adapters.docker_backend import DockerBackend
from wisp.benchmark.adapters.terminal_bench import (
    PTYRunner,
    TerminalBenchAdapter,
    TerminalBenchTask,
    sanitize_output,
    truncate_lines,
)


class ScriptedProvider:
    """Mock provider yielding canned commands, then DONE."""

    def __init__(self, commands: list[str]) -> None:
        self._commands = list(commands)
        self.calls = 0

    def generate(self, system_prompt: str, messages: list[dict],
                 tools=None) -> dict:
        self.calls += 1
        if self._commands:
            content = self._commands.pop(0)
        else:
            content = "DONE"
        return {"message": {"role": "assistant", "content": content}}


def _task(**overrides) -> TerminalBenchTask:
    base = {
        "task_id": "tbench-probe-1",
        "instruction": "Create hello.txt containing hi.",
        "verification_script": "test -f hello.txt && grep -q hi hello.txt",
    }
    base.update(overrides)
    return TerminalBenchTask.from_dict(base)


class TestHygiene:
    def test_sanitize_strips_all_control_forms(self) -> None:
        dirty = "\x1b[31mred\x1b[0m\x1b[2K\x1b[1;1Hhi\x07\x1b]0;title\x07ok\r\n"
        assert sanitize_output(dirty) == "redhiok\n"

    def test_sanitize_keeps_tabs_and_newlines(self) -> None:
        assert sanitize_output("a\tb\nc") == "a\tb\nc"

    def test_truncate_collapses_middle(self) -> None:
        text = "\n".join(f"line {i}" for i in range(150))
        out, truncated = truncate_lines(text)
        assert truncated is True
        assert out.startswith("line 0")
        assert out.endswith("line 149")
        assert "[... 50 lines truncated ...]" in out

    def test_truncate_short_passthrough(self) -> None:
        out, truncated = truncate_lines("a\nb")
        assert (out, truncated) == ("a\nb", False)

    def test_task_validation(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            TerminalBenchTask.from_dict({"task_id": "", "instruction": "x"})
        with pytest.raises(ValueError):
            TerminalBenchTask.from_dict({"task_id": "t"})
        t = TerminalBenchTask.from_dict({
            "task_id": "t", "instruction": "do it", "max_turns": 5})
        assert (t.max_turns, t.timeout_per_command) == (5, 45.0)


class TestLocalRun:
    def test_pass_task_end_to_end(self, tmp_path) -> None:
        provider = ScriptedProvider(["echo hi > hello.txt", "DONE"])
        adapter = TerminalBenchAdapter(provider, tmp_path)
        artifact = adapter.run(_task())
        assert artifact["resolved"] is True
        assert artifact["exit_code"] == 0
        assert artifact["commands_executed"] == ["echo hi > hello.txt"]
        assert artifact["total_turns"] == 2
        assert artifact["error_breakdown"] == {
            "deadlocks": 0, "syntax_errors": 0, "timeouts": 0}
        assert (tmp_path / "hello.txt").read_text().strip() == "hi"

    def test_artifact_file_schema(self, tmp_path) -> None:
        provider = ScriptedProvider(["DONE"])
        adapter = TerminalBenchAdapter(provider, tmp_path)
        artifact = adapter.run(_task(task_id="weird/id:1"))
        assert set(artifact.keys()) == {
            "task_id", "resolved", "exit_code", "total_turns",
            "total_tokens", "duration_seconds", "commands_executed",
            "error_breakdown", "trace"}
        path = tmp_path / "benchmark_results" / "terminal_bench" / "weird_id_1.json"
        assert path.exists()
        assert json.loads(path.read_text())["task_id"] == "weird/id:1"

    def test_failing_verification_marks_unresolved(self, tmp_path) -> None:
        provider = ScriptedProvider(["echo hi > hello.txt", "DONE"])
        adapter = TerminalBenchAdapter(provider, tmp_path)
        artifact = adapter.run(_task(verification_script="test -f nope.txt"))
        assert artifact["resolved"] is False
        assert artifact["exit_code"] != 0

    def test_deadlock_guard_fires_without_hanging(self, tmp_path) -> None:
        provider = ScriptedProvider(["cat", "DONE"])
        runner = PTYRunner(tmp_path, deadlock_after_s=1.0)
        adapter = TerminalBenchAdapter(provider, tmp_path, backend=runner)
        artifact = adapter.run(_task(timeout_per_command=20.0))
        assert artifact["error_breakdown"]["deadlocks"] == 1
        assert artifact["total_turns"] == 2  # loop survived to DONE
        assert "non-interactive" in artifact["trace"][0]["output"]

    def test_timeout_counts(self, tmp_path) -> None:
        provider = ScriptedProvider(["sleep 30", "DONE"])
        adapter = TerminalBenchAdapter(provider, tmp_path)
        artifact = adapter.run(_task(timeout_per_command=2.0))
        assert artifact["error_breakdown"]["timeouts"] == 1


class TestDockerBackend:
    def _fake(self, log: list, rc_map: dict | None = None):
        def exec_fn(cmd: list[str], timeout_s: float):
            log.append(cmd)
            joined = " ".join(cmd)
            if "docker info" in joined:
                return 0, "Server Version: 99", ""
            if "test -f" in joined or "grep" in joined:
                return 0, "", ""
            return 0, "fake-out", ""
        return exec_fn

    def test_lifecycle_setup_run_teardown(self, tmp_path) -> None:
        log: list = []
        backend = DockerBackend(image="img:test", workdir="/ws",
                                container_name="tbench-ut",
                                exec_fn=self._fake(log))
        backend.setup()
        assert any("run" in c and "tbench-ut" in " ".join(c) for c in log)
        res = backend.run("echo hi", timeout=10.0)
        assert (res.exit_code, res.output) == (0, "fake-out")
        assert any("exec" in c for c in log)
        backend.teardown()
        assert any("rm" in c for c in log)

    def test_setup_without_daemon_raises(self) -> None:
        backend = DockerBackend(
            image="img:test",
            exec_fn=lambda cmd, t: (1, "", "Cannot connect"))
        import pytest

        from wisp.benchmark.adapters.docker_backend import DockerUnavailable

        with pytest.raises(DockerUnavailable):
            backend.setup()

    def test_teardown_never_raises(self) -> None:
        def boom(cmd, timeout_s):
            raise RuntimeError("gone")

        backend = DockerBackend(image="img:test", container_name="x",
                                exec_fn=boom)
        backend.teardown()  # must not raise
