"""Lazy heavy imports: CLI launch must not pay for textual/requests.

Measured baseline (docs/repl-optimization-plan.md): the requests stack
(~73ms) and textual (~29ms) dominated warm import time while serving only
web tools and /tui respectively.
"""

from __future__ import annotations

import subprocess
import sys


class TestLazyHeavyImports:
    def test_entry_import_skips_textual_and_requests(self):
        code = (
            "import sys; import wisp.entry; "
            "assert 'textual' not in sys.modules, 'textual imported eagerly'; "
            "assert 'requests' not in sys.modules, 'requests imported eagerly'"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, f"stdout={proc.stdout} stderr={proc.stderr[-2000:]}"

    def test_tui_transport_importable_through_package(self):
        # Lazy __getattr__ must keep the public surface intact.
        import wisp.transport as t

        assert hasattr(t, "TUITransport")
        from wisp.transport.tui import TUITransport as Direct

        assert t.TUITransport is Direct

    def test_web_tools_resolve_lazily_but_are_callable(self):
        from wisp.tools.registry import TOOL_IMPLS

        for name in ("web_search", "web_fetch"):
            impl = TOOL_IMPLS.get(name)
            assert callable(impl), f"{name} missing from TOOL_IMPLS"

    def test_registry_import_skips_requests(self):
        code = (
            "import sys; import wisp.tools.registry; "
            "assert 'requests' not in sys.modules, 'requests imported eagerly'"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, f"stderr={proc.stderr[-2000:]}"
