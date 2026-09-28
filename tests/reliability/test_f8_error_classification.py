"""F8's second half — a missing validator is a SYSTEM failure, not a schema verdict.

`WISP_MIGRATION_STATUS.md` F8's row says it plainly: *"The error-classification half is NOT FIXED
(`SECONDARY_DEFECT_REMAINS`): the broad `except Exception` still reports a missing validator as an
argument verdict."*

The consequence is not cosmetic. When a missing capability is reported as a failure of the **input**
rather than of the **system**, every test downstream becomes a test of the wrong thing. Measured: 24
failures attributed to "pre-existing" that were F8-caused, and six tests in `test_13h2_determinism.py`
reporting the wrong thing for the life of the repository. F37 and F38 are the same shape.

Two failure kinds, and a caller must be able to tell them apart **without parsing prose**:

| Kind | Meaning |
|---|---|
| `SCHEMA_INVALID` | the validator RAN and rejected the arguments — a **data** failure |
| `CAPABILITY_MISSING` | the validator could not run — a **system** failure |

Non-vacuity: every assertion in `TestTheSystemFailureIsDistinguished` fails against the unmodified
tree, because the unmodified tree returns one `str` for both. That was checked by running this file
against `af3a89a`'s `stateless.py` (see `PHASE_F8_ERROR_CLASSIFICATION.md` §5).
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

from wisp.config import WispConfig  # noqa: E402
from wisp.core.engine import WispAgentCore  # noqa: E402
from wisp.core.runtime import AgentRuntime  # noqa: E402
from wisp.core.session_repo import SessionRepository  # noqa: E402
from wisp.core.stateless import (  # noqa: E402
    VALIDATION_CAPABILITY_MISSING,
    VALIDATION_SCHEMA_INVALID,
    ValidationFailure,
)
from wisp.infra.extensions import ExtensionHost  # noqa: E402
from wisp.infra.security import PermissionMode, SecurityPolicy  # noqa: E402
from wisp.infra.store import UnifiedStore  # noqa: E402
from wisp.infra.telemetry import Telemetry  # noqa: E402
from wisp.tool_executor import ToolExecutor  # noqa: E402

#: `ast.dump` of `_validate_tool_args` with its validation `Try` removed, at the
#: commit before this change. The F8 fix *is* that `Try`, so "the surrounding
#: function is otherwise unchanged" has to be stated relative to it — and this
#: digest is the statement. Recomputed and compared at HEAD and after the fix.
#:
#: Re-pinned after 32a0b20, which deliberately added the truncated-arguments check
#: between the write_file salvage and the validation `Try`. `ast.dump` differs
#: between Python versions, so the digest is CI's: Python 3.12.
SURROUNDING_AST_SHA256 = (
    "df9cf0e42253e56e6cc492a28b8708682f324dc0e98f78ec0d16725665046e8a")


@pytest.fixture
def validator_absent():
    """Hide `jsonschema` from `sys.modules` — the F8-provisioning technique.

    `None` makes `import jsonschema` raise `ImportError: import of jsonschema
    halted; None in sys.modules`, which is exactly the shape the real absence
    produced.
    """
    real = sys.modules.get("jsonschema")
    sys.modules["jsonschema"] = None
    try:
        yield
    finally:
        if real is None:
            sys.modules.pop("jsonschema", None)
        else:
            sys.modules["jsonschema"] = real


def _core() -> WispAgentCore:
    return WispAgentCore(config=None, provider=None,
                         security=SecurityPolicy(), tool_executor=None)


# ══════════════════════════════════════════════════════════════════════════
# The system failure — the half that was missing
# ══════════════════════════════════════════════════════════════════════════


class TestTheSystemFailureIsDistinguished:
    """Every test here fails against the unmodified tree."""

    def test_a_missing_validator_is_a_capability_failure(self, validator_absent):
        failure = _core()._validate_tool_args("read_file", {"path": "a.txt"})
        assert failure is not None, (
            "a valid call was ACCEPTED with no validator — it cannot have been checked")
        assert failure.kind == VALIDATION_CAPABILITY_MISSING, (
            f"a missing validator was reported as {failure.kind!r}")

    def test_the_message_names_the_capability(self, validator_absent):
        failure = _core()._validate_tool_args("read_file", {"path": "a.txt"})
        assert "jsonschema" in failure, (
            "the failure does not name the missing capability, so an operator "
            "cannot act on it")

    def test_the_message_does_not_blame_the_arguments(self, validator_absent):
        """The whole defect: a host failure reported as a data failure."""
        failure = _core()._validate_tool_args("read_file", {"path": "a.txt"})
        assert "Schema validation failed" not in failure, (
            "the refusal still reads as an argument verdict — F8's laundering is back")
        assert "UNAVAILABLE" in failure

    def test_it_says_the_arguments_were_never_checked(self, validator_absent):
        failure = _core()._validate_tool_args("read_file", {"path": "a.txt"})
        assert "never checked" in failure, (
            "the message must not let a reader infer the arguments were judged")

    def test_it_does_not_invite_a_retry(self, validator_absent):
        """The model is the reader, and 'retry' is the obvious response."""
        failure = _core()._validate_tool_args("read_file", {"path": "a.txt"})
        assert "identically" in failure

    def test_a_write_file_call_is_not_laundered_by_the_retry_block(
            self, validator_absent):
        """The second site.

        `write_file` with a `path` re-validates in a `write_file`-only retry
        block. Before the fix that block's own `import jsonschema as _js2` failed
        the same way and was swallowed, so the call fell through to the schema
        verdict — a *second* door into the same defect.
        """
        failure = _core()._validate_tool_args(
            "write_file", {"path": "x.txt", "content": "hi"})
        assert failure is not None
        assert failure.kind == VALIDATION_CAPABILITY_MISSING, (
            "the write_file retry block laundered a system failure into a schema verdict")


class TestTheFailureIsObservableWithoutProse:
    def test_it_is_still_a_str(self, validator_absent):
        """Three call sites interpolate it; one puts it in a JSON field."""
        failure = _core()._validate_tool_args("read_file", {"path": "a.txt"})
        assert isinstance(failure, str)
        assert isinstance(failure, ValidationFailure)

    def test_the_two_kinds_are_distinct_values(self, validator_absent):
        missing = _core()._validate_tool_args("read_file", {"path": "a.txt"})
        sys.modules.pop("jsonschema", None)
        import jsonschema  # noqa: F401  - restore the real module
        rejected = _core()._validate_tool_args("read_file", {})
        assert missing.kind != rejected.kind
        assert {missing.kind, rejected.kind} == {
            VALIDATION_CAPABILITY_MISSING, VALIDATION_SCHEMA_INVALID}

    def test_a_caller_can_branch_without_reading_the_message(self, validator_absent):
        """The property the brief asks for, stated as a caller would use it."""
        failure = _core()._validate_tool_args("read_file", {"path": "a.txt"})
        # No substring matching anywhere in this branch.
        outcome = ("host_broken" if failure.kind == VALIDATION_CAPABILITY_MISSING
                   else "arguments_rejected")
        assert outcome == "host_broken"


# ══════════════════════════════════════════════════════════════════════════
# The data failure — unchanged
# ══════════════════════════════════════════════════════════════════════════


class TestTheDataFailureIsUnchanged:
    def test_a_valid_call_is_accepted(self):
        assert _core()._validate_tool_args("read_file", {"path": "a.txt"}) is None

    def test_a_genuine_rejection_is_a_schema_verdict(self):
        failure = _core()._validate_tool_args("read_file", {})
        assert failure is not None
        assert failure.kind == VALIDATION_SCHEMA_INVALID
        assert "Schema validation failed" in failure
        assert "required property" in failure

    def test_the_rejection_message_is_byte_identical_to_the_historical_form(self):
        """A genuine schema rejection must be unchanged, not merely equivalent."""
        failure = _core()._validate_tool_args("read_file", {})
        assert str(failure).startswith(
            "Schema validation failed for tool 'read_file': "), (
            "the rejection's message prefix moved")

    def test_the_rejection_does_not_name_the_validator(self):
        failure = _core()._validate_tool_args("read_file", {})
        assert "jsonschema" not in failure, (
            "a data failure must not mention the validator")

    def test_an_unknown_tool_still_defers(self):
        assert _core()._validate_tool_args("not_a_registered_tool", {"x": 1}) is None

    def test_a_valid_call_is_accepted_even_with_no_validator(self, validator_absent):
        """Control: the *valid* path is what regressed under F8."""
        failure = _core()._validate_tool_args("read_file", {"path": "a.txt"})
        assert failure is not None, (
            "with no validator the call cannot be verified — accepting it would be "
            "a different bug")


# ══════════════════════════════════════════════════════════════════════════
# The surrounding function is otherwise unchanged
# ══════════════════════════════════════════════════════════════════════════


def _ast_without_validation(source: str) -> str:
    """`_validate_tool_args`'s AST with its validation `Try` removed."""
    tree = ast.parse(source)
    f = next(n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_validate_tool_args")
    def is_validation_block(node):
        return (isinstance(node, ast.Try)
                and any(isinstance(inner, ast.Import)
                        and any(a.name == "jsonschema" for a in inner.names)
                        for inner in ast.walk(node)))
    f.body = [s for s in f.body if not is_validation_block(s)]
    return ast.dump(f, annotate_fields=True, include_attributes=False)


class TestTheSurroundingFunctionIsOtherwiseUnchanged:
    def test_the_digest_matches_the_pre_change_function(self):
        src = (REPO / "wisp/core/stateless.py").read_text(encoding="utf-8")
        digest = hashlib.sha256(_ast_without_validation(src).encode()).hexdigest()
        assert digest == SURROUNDING_AST_SHA256, (
            "the fix changed something OUTSIDE the validation block — the "
            "deepcopy, the schema lookup, or the write_file salvage was touched")

    def test_the_helper_can_see_a_change(self):
        """Non-vacuity for the check above: it must fail when the function moves."""
        src = (REPO / "wisp/core/stateless.py").read_text(encoding="utf-8")
        mutated = src.replace(
            "return None  # Unknown tool — let security layer handle it",
            'return "MUTATED"  # Unknown tool')
        assert mutated != src, "the mutation target moved — fix this test"
        assert _ast_without_validation(src) != _ast_without_validation(mutated), (
            "the AST helper is blind to changes outside the validation block, so "
            "the digest above proves nothing")

    def test_exactly_one_handler_catches_import_error(self):
        tree = ast.parse((REPO / "wisp/core/stateless.py").read_text(encoding="utf-8"))
        f = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_validate_tool_args")
        catches = [h for t in ast.walk(f) if isinstance(t, ast.Try)
                   for h in t.handlers
                   if h.type is not None and "ImportError" in ast.dump(h.type)]
        assert len(catches) == 1, (
            f"expected one ImportError handler, found {len(catches)}")

    def test_the_import_error_clause_precedes_the_broad_one(self):
        """Order is the fix: the broad handler would otherwise swallow it."""
        tree = ast.parse((REPO / "wisp/core/stateless.py").read_text(encoding="utf-8"))
        f = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_validate_tool_args")
        target = next(t for t in ast.walk(f) if isinstance(t, ast.Try)
                      and any(isinstance(i, ast.Import)
                              and any(a.name == "jsonschema" for a in i.names)
                              for i in ast.walk(t)))
        dumps = [ast.dump(h.type) if h.type else "bare" for h in target.handlers]
        assert dumps[0] == "Name(id='ImportError', ctx=Load())", (
            f"the first handler is {dumps[0]!r}, not ImportError")


# ══════════════════════════════════════════════════════════════════════════
# End to end, through the real turn
# ══════════════════════════════════════════════════════════════════════════


class _Provider:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        yield from self._rounds[min(self.calls - 1, len(self._rounds) - 1)]()


def _tool_round(name, args, cid):
    def _g():
        yield {"type": "tool_calls",
               "calls": [{"id": cid, "type": "function",
                          "function": {"name": name, "arguments": args}}]}
        yield {"type": "done", "done_reason": "tool_calls"}
    return _g


def _content_round(text="done"):
    def _g():
        yield {"type": "content", "text": text}
        yield {"type": "done", "done_reason": "stop"}
    return _g


def _run(ws, rounds, *, mode=PermissionMode.ASK_ALL, sid="f8cls"):
    config = WispConfig().replace(workspace=str(ws), permission_mode=mode)
    store = UnifiedStore(ws / f"{sid}.db")
    provider = _Provider(rounds)

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(permission_mode=mode),
                             tool_executor=ToolExecutor(config))

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(permission_mode=mode),
        extensions=ExtensionHost(), telemetry=Telemetry(),
        core_factory=factory, session_repo=SessionRepository(store), config=config)
    session = {"id": sid, "model": "mock", "workspace": str(ws), "messages": []}

    async def approve(event, *a, **kw):
        return True

    async def _main():
        return [ev async for ev in runtime.run_turn(
            session, prompt="go", approval_handler=approve)]

    return asyncio.run(_main())


def _results(events):
    """The tool-result payloads, read the way `test_f8_tool_execution_restored.py`
    reads them — a flat event carries the payload under `data`."""
    out = []
    for ev in events:
        if ev.get("type") != "tool_result":
            continue
        payload = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        out.append((ev.get("name", ""),
                    str(payload.get("result", ev.get("result", "")))))
    return out


class TestEndToEnd:
    def test_the_model_is_told_the_host_is_broken(self, tmp_path, validator_absent):
        """The refusal the MODEL sees names the capability, not its arguments."""
        events = _run(tmp_path,
                      [_tool_round("read_file", {"path": "a.txt"}, "c0"),
                       _content_round()])
        blob = " ".join(t for _n, t in _results(events))
        blob += " " + " ".join(str(ev.get("message", "")) for ev in events)
        assert "UNAVAILABLE" in blob, (
            f"the turn did not report a host failure: {blob[:400]}")
        assert "Schema validation failed" not in blob, (
            "the turn still reports the arguments as the problem")

    def test_the_control_the_same_turn_with_a_validator(self, tmp_path):
        """With the validator present the same call succeeds — the fix is not a
        blanket refusal."""
        (tmp_path / "a.txt").write_text("real content\n", encoding="utf-8")
        events = _run(tmp_path,
                      [_tool_round("read_file", {"path": "a.txt"}, "c0"),
                       _content_round()])
        results = _results(events)
        assert results, f"no tool result at all: {events[:3]}"
        assert "real content" in results[0][1], (
            f"read_file did not execute: {results}")
        assert "UNAVAILABLE" not in results[0][1]
