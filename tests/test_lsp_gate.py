"""Tests for the LSP compiler gate (GH#28).

Hermetic: a fake sync server behind the REAL AsyncLSPClient proves the
adapter path; no language server binary needed.
"""

import asyncio

from wisp.core.lsp.client import AsyncLSPClient
from wisp.core.lsp.gate import (
    CompilerGate,
    format_lsp_frame,
    gate_from_manager,
    proposed_content,
)


def _diag(line0, char0, msg, severity=1):
    return {"range": {"start": {"line": line0, "character": char0}},
            "severity": severity, "message": msg}


class FakeServer:
    """Scripted sync server speaking AsyncLSPClient's method surface."""

    def __init__(self, diags=()):
        self.diags = list(diags)
        self.pushed: list[str] = []
        self.reverted_to_disk = False

    def ensure_document_open(self, path):
        return None

    def notify_text_change(self, path, text):
        self.pushed.append(text)

    def get_diagnostics(self, path):
        return list(self.diags)


def _gate(server, **kw):
    client = AsyncLSPClient(server, settle_s=0.1)

    async def _diagnose(abs_path, proposal):
        raw = await client.diagnose_proposal(abs_path, proposal, settle_s=0.0)
        return [
            {"range": {"start": {"line": d.line, "character": d.character}},
             "severity": d.severity, "message": d.message}
            for d in raw
        ]

    return CompilerGate(_diagnose, **kw)


def _run(coro):
    return asyncio.run(coro)


class TestFormat:
    def test_frame_shape(self):
        assert format_lsp_frame("pkg/core.py", 3, 10, "Type 'str' is bad\nsecond") == \
            "[LSP COMPILER ERROR] pkg/core.py:4:10 - Type 'str' is bad"


class TestProposedContent:
    def test_write_image(self):
        assert proposed_content("write", None, content="x=1") == "x=1"

    def test_edit_image(self):
        assert proposed_content("edit", "a=1\n", old_text="a=1", new_text="a=2") == "a=2\n"

    def test_edit_missing_anchor_skips(self):
        assert proposed_content("edit", "a=1\n", old_text="zzz", new_text="b") is None

    def test_read_never_gated(self):
        assert proposed_content("read", "a=1\n") is None


class TestGate:
    def test_error_blocks_with_frame(self):
        gate = _gate(FakeServer([_diag(3, 10, "Type 'str' is bad")]))
        verdict = _run(gate.check("pkg/core.py", "/ws/pkg/core.py", "x = 's'\n"))
        assert verdict.blocked is True
        assert "[LSP COMPILER ERROR] pkg/core.py:4:10 - Type 'str' is bad" in verdict.frame
        assert len(verdict.errors) == 1

    def test_warning_only_passes(self):
        gate = _gate(FakeServer([_diag(0, 0, "unused import", severity=2)]))
        assert _run(gate.check("a.py", "/ws/a.py", "x=1")).blocked is False

    def test_clean_passes_and_disk_untouched(self, tmp_path):
        target = tmp_path / "mod.py"
        target.write_text("x = 1\n")
        server = FakeServer([])
        gate = _gate(server)
        verdict = _run(gate.check("mod.py", str(target), "x = 'broken'\n"))
        assert verdict.blocked is False
        assert target.read_text() == "x = 1\n"  # proposal reverted, disk intact
        assert server.pushed[0] == "x = 'broken'\n"  # ...but server saw it

    def test_proposal_error_not_in_current_blocks(self):
        gate = _gate(FakeServer([_diag(0, 5, "undefined name 'y'")]))
        assert _run(gate.check("a.py", "/ws/a.py", "y = 1\n")).blocked is True

    def test_none_proposal_skips(self):
        gate = _gate(FakeServer([_diag(0, 0, "boom")]))
        assert _run(gate.check("a.py", "/ws/a.py", None)).blocked is False

    def test_server_exception_fails_open(self):
        class Exploding:
            def ensure_document_open(self, p):
                raise RuntimeError("dead server")

            def notify_text_change(self, p, t):
                raise RuntimeError("dead server")

            def get_diagnostics(self, p):
                raise RuntimeError("dead server")

        gate = _gate(Exploding())
        assert _run(gate.check("a.py", "/ws/a.py", "x=1")).blocked is False

    def test_timeout_fails_open(self):
        async def _slow(path, proposal):
            await asyncio.sleep(60)

        gate = CompilerGate(_slow, timeout_s=0.05)
        assert _run(gate.check("a.py", "/ws/a.py", "x=1")).blocked is False

    def test_error_cap_marks_remainder(self):
        gate = _gate(FakeServer([_diag(i, 0, f"e{i}") for i in range(8)]))
        verdict = _run(gate.check("a.py", "/ws/a.py", "x=1"))
        assert verdict.blocked and "... and 3 more errors" in verdict.frame


class FakeManager:
    def __init__(self, server=None):
        self._server = server

    def get_server_safe(self, path):
        return self._server


class TestFsMutateWiring:
    def test_write_blocked_by_gate(self, tmp_path):
        from wisp.tools._utils import set_lsp_manager
        from wisp.tools.errors import ToolError
        from wisp.tools.primitives import async_fs_mutate

        set_lsp_manager(FakeManager(FakeServer([_diag(0, 0, "bad type")])))
        try:
            try:
                _run(async_fs_mutate(
                    {"op": "write", "path": "m.py", "content": "x: int = 's'\n"},
                    str(tmp_path)))
                blocked = False
            except ToolError as exc:
                blocked = "LSP COMPILER ERROR" in str(exc)
            assert blocked is True
            assert not (tmp_path / "m.py").exists()  # never landed
        finally:
            set_lsp_manager(None)

    def test_no_manager_zero_behavior_change(self, tmp_path):
        from wisp.tools._utils import set_lsp_manager
        from wisp.tools.primitives import async_fs_mutate

        set_lsp_manager(None)
        out = _run(async_fs_mutate(
            {"op": "write", "path": "m.py", "content": "x = 1\n"}, str(tmp_path)))
        assert (tmp_path / "m.py").read_text() == "x = 1\n"
        assert "Wrote" in str(out)


class TestFromManager:
    def test_none_manager_skips(self):
        assert gate_from_manager(None) is None

    def test_no_server_for_file_passes(self):
        gate = gate_from_manager(FakeManager(None))
        assert gate is not None
        assert _run(gate.check("a.txt", "/ws/a.txt", "whatever")).blocked is False

    def test_real_path_blocks(self):
        gate = gate_from_manager(FakeManager(FakeServer([_diag(1, 2, "bad type")])), settle_s=0.1)
        assert gate is not None
        verdict = _run(gate.check("a.py", "/ws/a.py", "x: int = 's'"))
        assert verdict.blocked is True
        assert "a.py:2:2" in verdict.frame
