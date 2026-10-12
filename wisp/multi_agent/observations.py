"""What the harness SAW a child do: tool results, as the executor returned them.

A child's reply is the model's text and is data. What it did is not: every `tool_result` event comes from the ToolExecutor, with the tool's name and the text the
tool itself returned. This module keeps a bounded, classified record of those events so that a verdict can be checked against them (`wisp.graph.evidence`)
instead of against the child's own account of what it observed.

Only the harness's reading of each result is kept, not the result: for `run_tests` whether it was a real green suite (the one reading shared with the completion
gate: `_tool_result_output` unwraps the executor's envelope, `_run_tests_is_evidence` reads the text), for every other tool nothing but its name.
"""

from __future__ import annotations

from typing import Any

#: Everything that loops has a bound. Only the tail matters to a verdict (the LAST test run, and whether anything changed after it), so the oldest are dropped.
MAX_OBSERVATIONS = 200
_NAME_CHARS = 64


def record(observations: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """Append one observation for a `tool_result` event. Never raises: recording must not fail a child's run."""
    try:
        name = str(event.get("name") or "")[:_NAME_CHARS]
        entry: dict[str, Any] = {"tool": name}
        if name == "run_tests":
            from wisp.core.stateless import _tool_result_output
            from wisp.core.verification import _run_tests_is_evidence

            output = _tool_result_output(event.get("result", ""))  # the same unwrapping the completion gate does: a refusal or an error carries no output
            entry["green"] = isinstance(output, str) and _run_tests_is_evidence(output)
        observations.append(entry)
        if len(observations) > MAX_OBSERVATIONS:
            del observations[: len(observations) - MAX_OBSERVATIONS]
    except Exception:  # noqa: BLE001 — a record that cannot be made is an absent record, which the verdict check reads as no evidence
        return
