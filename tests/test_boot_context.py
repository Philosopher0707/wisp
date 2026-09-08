"""GH#22: Turn-0 boot context pins.

Ordering, budget truncation, non-git/missing fallbacks, static-prefix
equality across turns, and seed placement (first, once, never for
subagents). No network, no daemon, hermetic workspaces.
"""

from __future__ import annotations

import pytest

from wisp.core.context.boot import (
    BUDGET_CHARS,
    BootContextAssembler,
    TRUNCATION_NOTE,
    _clean_cut,
)


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / ".wisp").mkdir(exist_ok=True)
    return tmp_path


class TestDiscovery:
    def test_prefers_agents_over_claude(self, ws) -> None:
        (ws / "AGENTS.md").write_text("agents rules")
        (ws / "CLAUDE.md").write_text("claude rules")
        source, text = BootContextAssembler(ws).discover()
        assert (source, text) == ("AGENTS.md", "agents rules")

    def test_falls_back_to_claude_then_instructions(self, ws) -> None:
        (ws / "CLAUDE.md").write_text("claude rules")
        assert BootContextAssembler(ws).discover()[0] == "CLAUDE.md"
        (ws / "CLAUDE.md").unlink()
        (ws / ".wisp" / "instructions.md").write_text("wisp rules")
        assert BootContextAssembler(ws).discover() == (
            ".wisp/instructions.md", "wisp rules")

    def test_missing_everything(self, ws) -> None:
        assert BootContextAssembler(ws).discover() == (None, "")

    def test_broken_symlink_and_unreadable_never_raise(self, ws) -> None:
        import os

        (ws / "AGENTS.md").symlink_to(ws / "does-not-exist")
        (ws / "CLAUDE.md").write_text("fallback ok")
        assert BootContextAssembler(ws).discover()[0] == "CLAUDE.md"
        os.chmod(ws / "CLAUDE.md", 0)
        try:
            source, _ = BootContextAssembler(ws).discover()
            assert source in (None, "CLAUDE.md", ".wisp/instructions.md")
        finally:
            os.chmod(ws / "CLAUDE.md", 0o644)

    def test_non_utf8_decodes_with_replacement(self, ws) -> None:
        (ws / "AGENTS.md").write_bytes(b"rules \xff\xfe here")
        source, text = BootContextAssembler(ws).discover()
        assert source == "AGENTS.md" and "rules" in text


class TestTruncation:
    def test_short_text_untouched(self) -> None:
        assert _clean_cut("short", 8000) == ("short", False)

    def test_long_text_cuts_at_boundary_with_note(self) -> None:
        paras = [f"Paragraph {i} with several complete sentences. Yes indeed."
                 for i in range(200)]
        text = "\n\n".join(paras)
        assert len(text) > BUDGET_CHARS
        cut, truncated = _clean_cut(text, BUDGET_CHARS)
        assert truncated is True
        assert cut.endswith(TRUNCATION_NOTE)
        assert len(cut) < len(text)
        # Boundary-clean: never ends mid-word.
        assert not cut[: -len(TRUNCATION_NOTE)].endswith("Para")

    def test_assemble_applies_budget(self, ws) -> None:
        (ws / "AGENTS.md").write_text("# Big\n\n" + ("x " * 6000))
        payload = BootContextAssembler(ws).assemble()
        assert payload.status == "found"
        assert TRUNCATION_NOTE in payload.guidelines
        assert payload.est_tokens <= 2000 + 50


class TestPosture:
    def test_non_git_fallback(self, ws) -> None:
        posture = BootContextAssembler(ws).git_posture()
        assert posture["branch"] == "non-git"

    def test_git_posture_shape(self) -> None:
        import subprocess
        from pathlib import Path
        import tempfile

        repo = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=30)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-q", "--allow-empty",
                        "-m", "init"], check=True, timeout=30)
        posture = BootContextAssembler(repo).git_posture()
        assert posture["commit"] not in ("", "unknown")
        assert posture["dirty"] in ("clean", "dirty")
        (repo / "new.txt").write_text("x")
        assert BootContextAssembler(repo).git_posture()["dirty"] == "dirty"

    def test_sandbox_posture_values(self, ws) -> None:
        assert BootContextAssembler(ws).sandbox_posture() in (
            "docker", "host-unconfined", "unknown")


class TestMemoryBullets:
    def test_workspace_before_global_newest_first(self, ws, monkeypatch) -> None:
        import wisp.memory as mem_mod

        monkeypatch.setattr(mem_mod, "load_memory", lambda: {
            "global_facts": [
                {"content": "old global", "added": "2020-01-01T00:00:00"},
                {"content": "new global", "added": "2024-01-01T00:00:00"},
            ],
            "workspace_facts": {str(ws.resolve()): [
                {"content": "ws fact", "added": "2023-01-01T00:00:00"}]},
        })
        asm = BootContextAssembler(ws, memory_limit=2)
        bullets = asm.memory_bullets()
        assert bullets[0] == "new global"
        assert len(bullets) == 2

    def test_memory_failure_is_empty(self, ws, monkeypatch) -> None:
        import wisp.memory as mem_mod

        monkeypatch.setattr(mem_mod, "load_memory",
                            lambda: (_ for _ in ()).throw(IOError("disk")))
        assert BootContextAssembler(ws).memory_bullets() == []


class TestSeedPlacement:
    def _core(self):
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.providers.mock import MockProvider

        return WispAgentCore(config=WispConfig(), provider=MockProvider(
            responses=["turn answer"]))

    def _session(self, ws, messages=None):
        return {"id": "s-boot", "workspace": str(ws),
                "messages": list(messages or [])}

    def test_seed_first_and_guidelines_split(self, ws) -> None:
        import asyncio

        from wisp.providers.mock import MockProvider

        (ws / "AGENTS.md").write_text("# Rules\nAlways be precise.")
        seen: list = []

        class Recording(MockProvider):
            def generate_stream_events(self, system_prompt, messages,
                                       tools=None, checkpoint_every=50):
                seen.append((system_prompt, list(messages)))
                yield from super().generate_stream_events(
                    system_prompt, messages, tools, checkpoint_every)

        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore

        core = WispAgentCore(config=WispConfig(), provider=Recording(
            responses=["turn answer"]))
        session = self._session(ws)

        async def drive():
            async for _ in core.turn(session, "do the thing"):
                pass

        asyncio.run(drive())
        assert seen, "provider never saw messages"
        system_prompt, messages = seen[0]
        # Seed is FIRST, user prompt second.
        assert messages[0]["role"] == "user"
        assert "Always be precise" in messages[0]["content"]
        assert messages[1] == {"role": "user", "content": "do the thing"}
        # Static prefix carries no guidelines (the KV-cache split).
        assert "Always be precise" not in system_prompt

    def test_no_history_no_seed_without_substance(self, ws, monkeypatch) -> None:
        # Isolate from the developer's real global memory: substance must
        # come from the workspace under test, nowhere else.
        import wisp.memory as mem_mod

        monkeypatch.setattr(mem_mod, "load_memory",
                            lambda: {"global_facts": [], "workspace_facts": {}})
        asm = BootContextAssembler(ws, repomap_chars=0)
        assert asm.seed_message() is None

    def test_resume_with_history_skips_seed(self, ws) -> None:
        import asyncio

        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.providers.mock import MockProvider

        (ws / "AGENTS.md").write_text("# Rules\nAlways be precise.")
        seen: list = []

        class Recording(MockProvider):
            def generate_stream_events(self, system_prompt, messages,
                                       tools=None, checkpoint_every=50):
                seen.append(list(messages))
                yield from super().generate_stream_events(
                    system_prompt, messages, tools, checkpoint_every)

        core = WispAgentCore(config=WispConfig(), provider=Recording(
            responses=["again"]))
        history = [
            {"role": "user", "content": "[Session boot context — old seed]"},
            {"role": "user", "content": "do the thing"},
            {"role": "assistant", "content": "turn answer"},
        ]
        session = self._session(ws, history)

        async def drive():
            async for _ in core.turn(session, "follow up"):
                pass

        asyncio.run(drive())
        assert seen
        seeds = [m for m in seen[0]
                 if m.get("role") == "user"
                 and "Session boot context" in m.get("content", "")]
        assert len(seeds) == 1  # the persisted one; no duplicate injected

    def test_subagent_sessions_stay_lean(self, ws) -> None:
        import asyncio

        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.providers.mock import MockProvider

        (ws / "AGENTS.md").write_text("# Rules\nAlways be precise.")
        seen: list = []

        class Recording(MockProvider):
            def generate_stream_events(self, system_prompt, messages,
                                       tools=None, checkpoint_every=50):
                seen.append(list(messages))
                yield from super().generate_stream_events(
                    system_prompt, messages, tools, checkpoint_every)

        core = WispAgentCore(config=WispConfig(), provider=Recording(
            responses=["child answer"]))
        session = self._session(ws)
        session["subagent_system_prompt"] = "you are a child"

        async def drive():
            async for _ in core.turn(session, "child task"):
                pass

        asyncio.run(drive())
        assert seen
        assert not any("Session boot context" in m.get("content", "")
                       for m in seen[0] if m.get("role") == "user")


class TestStaticPrefixEquality:
    def test_system_prompt_identical_across_turns(self, ws) -> None:
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.providers.mock import MockProvider

        (ws / "AGENTS.md").write_text("# Rules\nStable.")
        core = WispAgentCore(config=WispConfig(),
                             provider=MockProvider(responses=["x"]))
        session = {"id": "s-eq", "workspace": str(ws), "messages": []}
        first = core._build_system_prompt(session)
        session["messages"].append({"role": "user", "content": "one"})
        session["messages"].append({"role": "assistant", "content": "two"})
        second = core._build_system_prompt(session)
        assert first == second


class TestDoctorBoot:
    def test_boot_check_reports_found(self, ws, monkeypatch) -> None:
        import asyncio

        (ws / "AGENTS.md").write_text("# Rules\nHi.")
        monkeypatch.chdir(ws)
        from wisp.core.doctor import _check_boot_context

        result = asyncio.run(_check_boot_context())
        assert result.ok
        assert result.details["source"] == "AGENTS.md"
        assert result.details["status"] == "found"
        assert result.details["bytes"] > 0
        assert result.details["est_tokens"] > 0

    def test_boot_check_reports_none(self, ws, monkeypatch) -> None:
        import asyncio

        monkeypatch.chdir(ws)
        from wisp.core.doctor import _check_boot_context

        result = asyncio.run(_check_boot_context())
        assert result.ok  # informational, never a failure
        assert result.details["status"] == "none"
