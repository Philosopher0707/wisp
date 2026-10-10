"""What compaction must never lose, written by the harness from the tool calls and not by a model.

A summary written by a model is only as good as that model's attention, and with no summarizer configured (the default) there was no summary at all: history
became `[Compacted N messages]`. This record is a deterministic read of the messages being dropped: what the user asked, which files were written or edited,
and which commands counted as verification, with their result. It says plainly that old results describe old code. Pure; no I/O.
"""

from __future__ import annotations

import json
from typing import Any

from wisp.core.gates.verify import classify
from wisp.core.verification import _run_tests_is_evidence, _verify_result_is_success  # one reading of "did it pass", shared with the completion gate

MAX_FILES = 40
MAX_RUNS = 12
MAX_ASKS = 5
ASK_CHARS = 240
COMMAND_CHARS = 160

_WRITE_TOOLS = frozenset({"write_file", "edit_file", "edit_file_multi", "fs_mutate"})
_SHELL_TOOLS = frozenset({"run_bash", "exec_sandbox"})
_PATH_KEYS = ("path", "file_path", "file", "filename")


def _args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def _paths(args: dict[str, Any]) -> list[str]:
    found = [str(args[k]) for k in _PATH_KEYS if isinstance(args.get(k), str) and args[k]]
    for edit in args.get("edits") or []:
        if isinstance(edit, dict):
            found += [str(edit[k]) for k in _PATH_KEYS if isinstance(edit.get(k), str) and edit[k]]
    return found


def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _more(items: list[str], limit: int) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" … and {len(items) - limit} more" if len(items) > limit else "")


def build_record(messages: list[dict[str, Any]]) -> str:
    """The record for `messages`, or "" when they hold nothing worth recording."""
    pending: dict[str, tuple[str, dict[str, Any]]] = {}
    asks: list[str] = []
    files: list[str] = []
    runs: list[dict[str, Any]] = []
    edits_seen = 0
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            ask = _trim(_text(msg.get("content")), ASK_CHARS)
            if ask:
                asks.append(ask)
        elif role == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name, args = str(fn.get("name", "")), _args(fn.get("arguments"))
                pending[str(tc.get("id", ""))] = (name, args)
                if name in _WRITE_TOOLS:
                    edits_seen += 1
                    files += [p for p in _paths(args) if p not in files]
        elif role == "tool":
            name, args = pending.get(str(msg.get("tool_call_id", "")), ("", {}))
            content = _text(msg.get("content"))
            if name in _SHELL_TOOLS and isinstance(args.get("command"), str):
                verdict = classify(args["command"])
                if verdict.ok:
                    runs.append({"what": _trim(args["command"], COMMAND_CHARS), "passed": _verify_result_is_success(content), "edits_before": edits_seen})
            elif name == "run_tests" and _run_tests_is_evidence(content):
                runs.append({"what": "run_tests " + _trim(" ".join(str(v) for v in args.values()), 80), "passed": True, "edits_before": edits_seen})
            elif name == "run_tests" and "## Test Results (" in content and "- Failed: 0, Errors: 0" not in content:
                runs.append({"what": "run_tests " + _trim(" ".join(str(v) for v in args.values()), 80), "passed": False, "edits_before": edits_seen})
    if not (asks or files or runs):
        return ""
    lines = ["[Harness record of the messages compacted away. Written from the tool calls, not by a model; it lists what happened, not whether it was right.]"]
    if asks:
        lines.append("What the user asked (oldest first, trimmed):")
        lines += [f"- {a}" for a in asks[-MAX_ASKS:]]
    if files:
        lines.append(f"Files written or edited ({len(files)}): {_more(files, MAX_FILES)}")
    if runs:
        lines.append("Verification runs that count as evidence, and their result:")
        for r in runs[-MAX_RUNS:]:
            stale = "; edited after this run, so this no longer describes the current code" if edits_seen > r["edits_before"] else ""
            lines.append(f"- {r['what']} → {'passed' if r['passed'] else 'FAILED'}{stale}")
        lines.append("These results are old. Before claiming the work passes, run it again on the current code.")
    return "\n".join(lines)


def attach_record(summary: str, compacted: list[dict[str, Any]], summarizer_ran: bool) -> str:
    """`summary` with the harness record after it. Without a summarizer the header says the conversation itself is gone, not just shortened."""
    record = build_record([m for m in compacted if m.get("role") != "system"])
    if summarizer_ran:
        return f"{summary}\n\n{record}" if record else summary
    gone = f"[Compacted {len(compacted)} messages. No summarizer was available, so what was said is gone.]"
    return f"{gone}\n{record}" if record else gone
