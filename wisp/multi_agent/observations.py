"""What the harness SAW a child do: tool results, as the executor returned them.

A child's reply is the model's text and is data. What it did is not: every `tool_result` event comes from the ToolExecutor, with the tool's name and the text the
tool itself returned. This module keeps a bounded, classified record of those events so that a verdict can be checked against them (`wisp.graph.evidence`)
instead of against the child's own account of what it observed.

Only the harness's reading of each result is kept, not the result: for `run_tests` whether it was a real green suite (the one reading shared with the completion
gate: `_tool_result_output` unwraps the executor's envelope, `_run_tests_is_evidence` reads the text); for a file-writing tool the path its call named and whether
the tool succeeded (design S4: the files a child changed come from here, never from its prose); for every other tool nothing but its name.
"""

from __future__ import annotations

from typing import Any

#: Everything that loops has a bound. Only the tail matters to a verdict (the LAST test run, and whether anything changed after it), so the oldest are dropped.
MAX_OBSERVATIONS = 200
_NAME_CHARS = 64
_PATH_CHARS = 512
#: Tools whose call names one file it writes (`path` in each schema).
FILE_WRITERS = frozenset({"write_file", "edit_file", "edit_file_multi"})


def note_call(pending: dict[str, list[str]], event: dict[str, Any]) -> None:
    """Remember the path a file-writing `tool_call` names, for the `tool_result` that answers it."""
    try:
        name = str(event.get("name") or "")
        args = event.get("arguments")
        if name in FILE_WRITERS and isinstance(args, dict) and isinstance(args.get("path"), str):
            pending.setdefault(name, []).append(args["path"][:_PATH_CHARS])
    except Exception:  # noqa: BLE001 — see record()
        return


def record(observations: list[dict[str, Any]], event: dict[str, Any],
           pending: dict[str, list[str]] | None = None) -> None:
    """Append one observation for a `tool_result` event. Never raises: recording must not fail a child's run."""
    try:
        name = str(event.get("name") or "")[:_NAME_CHARS]
        entry: dict[str, Any] = {"tool": name}
        if name == "run_tests" or name in FILE_WRITERS:
            from wisp.core.stateless import _tool_result_output

            output = _tool_result_output(event.get("result", ""))  # the same unwrapping the completion gate does: a refusal or an error carries no output
        if name == "run_tests":
            from wisp.core.verification import _run_tests_is_evidence

            entry["green"] = isinstance(output, str) and _run_tests_is_evidence(output)
        if name in FILE_WRITERS and pending and pending.get(name):
            entry["path"] = pending[name].pop(0)
            entry["ok"] = output is not None
        observations.append(entry)
        if len(observations) > MAX_OBSERVATIONS:
            del observations[: len(observations) - MAX_OBSERVATIONS]
    except Exception:  # noqa: BLE001 — a record that cannot be made is an absent record, which the verdict check reads as no evidence
        return


def clean(observations: Any) -> list[dict[str, Any]]:
    """The well-formed entries of an observation list (anything else is no evidence)."""
    if not isinstance(observations, (list, tuple)):
        return []
    return [o for o in observations if isinstance(o, dict) and isinstance(o.get("tool"), str) and o["tool"]]


def tests_state(observations: Any) -> str:
    """``none`` | ``red`` | ``stale`` | ``green``: the LAST run_tests result, and whether anything that can change files ran after it."""
    from wisp.core.verification import _MUTATING_TOOLS, _VERIFY_TOOLS

    seen = clean(observations)
    last = None
    for index, entry in enumerate(seen):
        if entry["tool"] == "run_tests":
            last = index
    if last is None:
        return "none"
    if seen[last].get("green") is not True:
        return "red"
    if any(entry["tool"] in (_MUTATING_TOOLS | _VERIFY_TOOLS) for entry in seen[last + 1:]):
        return "stale"
    return "green"


def written_paths(observations: Any) -> list[str]:
    """Files a child's own tool calls wrote, in first-write order: only calls whose result the harness saw succeed."""
    out: list[str] = []
    for entry in clean(observations):
        path = entry.get("path")
        if entry.get("ok") is True and isinstance(path, str) and path and path not in out:
            out.append(path)
    return out
