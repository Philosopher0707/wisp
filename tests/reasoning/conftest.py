from __future__ import annotations

import pytest

from wisp.core.reasoning.ledger import Ledger, ObservedEvent


def ev(name: str, source: str, *, command: str | None = None, text: str = "", paths: tuple[str, ...] = (), failed: bool = False, denied: bool = False) -> ObservedEvent:
    return ObservedEvent("tool_result", name, source, result_text=text, command=command, paths=paths, failed=failed, denied=denied)


FAIL = "[exit code: 1]\nFAILED tests/test_x.py::test_a"


@pytest.fixture
def ledger() -> Ledger:
    return Ledger()
