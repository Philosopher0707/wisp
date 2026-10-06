from __future__ import annotations

import os

import pytest

from wisp.core.gates import GateContext, GateMode


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    (root / "sub").mkdir(parents=True)
    return os.path.realpath(root)


@pytest.fixture
def make_ctx(ws):
    def _make(**kw):
        return GateContext(workspace=kw.pop("workspace", ws), home=kw.pop("home", "/Users/tester"), **kw)

    return _make


@pytest.fixture
def ctx(make_ctx):
    return make_ctx()


def rules(violations):
    return sorted({v.rule for v in violations})


def layers(violations):
    return sorted({v.layer for v in violations})


import contextlib
import signal


@contextlib.contextmanager
def alarm(seconds: int):
    """Fail the test, instead of hanging the suite, if the block does not finish. (POSIX only, like the rest of the harness.)"""

    def _boom(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"did not finish within {seconds}s")

    old = signal.signal(signal.SIGALRM, _boom)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
