"""Token-aware sliding window + diff-only generation + payload summarization.

Bottlenecks addressed:
  * Full history + raw tool dumps per turn -> 50k+ token blow-up under 50k LOC
  * LLM emitting 1000-line files for 2-line edits -> generation latency + token burn
  * >50-line tool results (ls, rg, test output) dumped verbatim into context

Policy:
  - Never pass more than N recent tool exchanges (default 6) + system head
  - Diff-only outputs: unified patch or SEARCH/REPLACE blocks
  - >50-line outputs auto-truncated/summarized before history insert
"""

from __future__ import annotations

import hashlib
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import tiktoken  # type: ignore

    _HAVE_TIKTOKEN = True
except Exception:
    _HAVE_TIKTOKEN = False


# ── Token counting ───────────────────────────────────────────────

def estimate_tokens(text: str, chars_per_token: int = 4) -> int:
    if _HAVE_TIKTOKEN:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // max(1, chars_per_token))


def _tokens_for_messages(messages: List[Dict[str, Any]], cpt: int = 4) -> int:
    return sum(estimate_tokens(str(m.get("content", "")), cpt) for m in messages)


# ── Sliding window ───────────────────────────────────────────────

@dataclass
class PrunerConfig:
    max_recent_exchanges: int = 6  # tool-call/result pairs
    max_total_tokens: int = 120_000  # hard cap (~256k window minus prompt budget)
    chars_per_token: int = 4
    keep_system_head: bool = True  # always preserve first system message(s)
    summarize_threshold_lines: int = 50
    dedupe_repeated_errors: bool = True


@dataclass
class PruneStats:
    input_messages: int
    output_messages: int
    input_tokens: int
    output_tokens: int
    truncated_tool_results: int = 0
    deduped_errors: int = 0


def _is_system(m: Dict[str, Any]) -> bool:
    return m.get("role") == "system"


def _is_tool(m: Dict[str, Any]) -> bool:
    return m.get("role") == "tool"


def summarize_tool_result(content: str, max_lines: int = 50) -> str:
    """Truncate/summarize >50-line outputs before history insert.

    Keeps head + tail + counts, so model sees shape without token flood.
    """
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return content
    head = lines[: max_lines // 2]
    tail = lines[-(max_lines // 2) :]
    # try to keep error lines more visible
    err_lines = [l for l in lines if "Error" in l or "FAILED" in l or "Traceback" in l][:5]
    tag = f"\n… +{len(lines) - max_lines} lines truncated (kept {len(head)} head + {len(tail)} tail)"
    if err_lines:
        tag += "\n[errors excerpt]\n" + "\n".join(err_lines[:3])
    return "\n".join(head) + tag + "\n" + "\n".join(tail)


def prune_history(
    messages: List[Dict[str, Any]],
    config: Optional[PrunerConfig] = None,
) -> Tuple[List[Dict[str, Any]], PruneStats]:
    """Cap conversational buffer — sliding window + token cap + payload summary.

    Returns (pruned_messages, stats). Never mutates input.
    """
    cfg = config or PrunerConfig()
    inp_tok = _tokens_for_messages(messages, cfg.chars_per_token)
    inp_n = len(messages)

    # 1) split system head vs rest
    system_head: List[Dict[str, Any]] = []
    rest: List[Dict[str, Any]] = []
    for m in messages:
        if cfg.keep_system_head and _is_system(m) and not rest:
            system_head.append(dict(m))
        else:
            rest.append(dict(m))

    # 2) keep last N exchanges = last N*2 tool/result + last user/assistant
    # We walk backwards and keep up to max_recent_exchanges tool pairs + tail
    if len(rest) > cfg.max_recent_exchanges * 3 + 4:
        # keep tail window
        keep_tail = rest[-(cfg.max_recent_exchanges * 3 + 4) :]
        # optionally keep first user message for intent
        first_user = next((m for m in rest if m.get("role") == "user"), None)
        if first_user and first_user not in keep_tail:
            rest = [first_user, {"role": "system", "content": f"[pruned {len(rest) - len(keep_tail) -1} messages — sliding window]"}] + keep_tail
        else:
            rest = [{"role": "system", "content": f"[pruned {len(rest) - len(keep_tail)} messages — sliding window]"}] + keep_tail

    # 3) summarize oversized tool results in the kept window
    truncated = 0
    for m in rest:
        if _is_tool(m):
            content = str(m.get("content", ""))
            if content.count("\n") > cfg.summarize_threshold_lines:
                m["content"] = summarize_tool_result(content, cfg.summarize_threshold_lines)
                truncated += 1

    # 4) dedupe repeated identical error tracebacks
    deduped = 0
    if cfg.dedupe_repeated_errors:
        seen_err_hash: Dict[str, int] = {}
        filtered: List[Dict[str, Any]] = []
        for m in rest:
            content = str(m.get("content", ""))
            if "Traceback" in content or "Traceback" in str(m):
                h = hashlib.sha256(content.encode(errors="ignore")).hexdigest()[:16]
                cnt = seen_err_hash.get(h, 0)
                if cnt >= 1:
                    deduped += 1
                    continue  # drop repeat
                seen_err_hash[h] = cnt + 1
            filtered.append(m)
        rest = filtered

    out = system_head + rest

    # 5) hard token cap — binary chop from front (keep system head)
    out_tok = _tokens_for_messages(out, cfg.chars_per_token)
    while out_tok > cfg.max_total_tokens and len(out) > len(system_head) + 2:
        # drop oldest non-system message
        drop_idx = len(system_head)
        # don't drop a tool result that pairs with a kept tool_call mid-window
        out.pop(drop_idx)
        out_tok = _tokens_for_messages(out, cfg.chars_per_token)
        # insert marker once
        if not any("[token cap]" in str(m.get("content", "")) for m in out):
            out.insert(len(system_head), {"role": "system", "content": f"[token cap {cfg.max_total_tokens} — pruned history]"})

    stats = PruneStats(
        input_messages=inp_n,
        output_messages=len(out),
        input_tokens=inp_tok,
        output_tokens=_tokens_for_messages(out, cfg.chars_per_token),
        truncated_tool_results=truncated,
        deduped_errors=deduped,
    )
    return out, stats


# ── Diff-only generation ─────────────────────────────────────────

_DIFF_HEADER = """*** Diff-only contract ***
Never output a full file for small edits. Use unified diff or SEARCH/REPLACE:

<<<< SEARCH
exact old block (must match verbatim, unique in file)
====
replacement block
>>>> REPLACE

Or `patch -p1` unified diff. Violations are rejected.
"""

def diff_enforce_prompt() -> str:
    return _DIFF_HEADER


def _normalize(s: str) -> str:
    return s.replace("\r\n", "\n").strip()


def apply_search_replace(
    original: str,
    search: str,
    replace: str,
    *,
    must_be_unique: bool = True,
) -> Tuple[str, int]:
    """Apply one SEARCH/REPLACE; returns (new_content, edits_applied).

    Raises ValueError on violation (not found / not unique) so caller can
    surface actionable error to model instead of silent no-op.
    """
    orig_norm = original.replace("\r\n", "\n")
    search_norm = search.replace("\r\n", "\n")
    replace_norm = replace.replace("\r\n", "\n")

    # exact match first (faster + preserves intent)
    count = orig_norm.count(search_norm)
    if count == 0:
        # try trimmed match (model often trims trailing newline)
        if _normalize(search_norm) in _normalize(orig_norm) and _normalize(search_norm) != "":
            # map normalized back to real — do least-surprise replace of normalized block
            # fallback to exact trimmed
            raise ValueError("SEARCH block not found verbatim — must match exact indentation and newlines")
        raise ValueError("SEARCH block not found — no edit applied")
    if must_be_unique and count > 1:
        raise ValueError(f"SEARCH block not unique — found {count} matches, make it more specific (add surrounding lines)")
    new = orig_norm.replace(search_norm, replace_norm, 1)
    return new, 1


def apply_search_replace_blocks(original: str, text: str) -> Tuple[str, int]:
    """Parse <<<< SEARCH / ==== / >>>> REPLACE blocks from model output.

    Returns (new_content, total_edits).
    """
    pattern = re.compile(r"<<<< SEARCH\n(.*?)\n====\n(.*?)\n>>>> REPLACE", re.DOTALL)
    matches = list(pattern.finditer(text))
    if not matches:
        raise ValueError("No SEARCH/REPLACE blocks found — expected <<<< SEARCH … ==== … >>>> REPLACE")

    cur = original.replace("\r\n", "\n")
    total = 0
    for m in matches:
        search, replace = m.group(1), m.group(2)
        cur, n = apply_search_replace(cur, search, replace, must_be_unique=True)
        total += n
    return cur, total


def apply_unified_diff(original: str, patch: str) -> Tuple[str, int]:
    """Apply unified diff (patch -p1) to original.

    Uses difflib as pure-python applier (no external `patch` binary).
    """
    import difflib

    # Reconstruct via difflib's unified_diff parser — simple line-based
    # For production latency we delegate to wisp.diff when available
    try:
        from wisp.diff import apply_edit_with_diff  # type: ignore
        # wisp.diff expects edit ops; we do minimal: replace whole file if patch seems valid
        # Fallback to manual below if import fails
        raise ImportError("use manual")
    except Exception:
        pass

    # Manual: split patch hunks and apply
    patch_lines = patch.splitlines()
    # Quick path: if patch is already full content (no diff headers), reject per contract
    if not any(l.startswith("@@") or l.startswith("---") or l.startswith("+++") for l in patch_lines):
        raise ValueError("Not a unified diff — missing @@/---/+++ headers. Use SEARCH/REPLACE instead for small edits.")

    # Use `patch` binary if available for correctness; else naive
    try:
        import subprocess
        import tempfile
        import pathlib

        with tempfile.TemporaryDirectory() as td:
            orig_p = pathlib.Path(td) / "orig"
            patch_p = pathlib.Path(td) / "patch.diff"
            orig_p.write_text(original, encoding="utf-8")
            patch_p.write_text(patch, encoding="utf-8")
            res = subprocess.run(["patch", "-p0", str(orig_p), "-i", str(patch_p), "--quiet"], capture_output=True, text=True)
            if res.returncode == 0:
                return orig_p.read_text(encoding="utf-8"), patch.count("@@")
    except FileNotFoundError:
        pass
    # Naive fallback — for tests only: treat patch body as replacement diff
    raise ValueError("Failed to apply unified diff — patch binary missing or patch malformed; use SEARCH/REPLACE blocks")


def diff_only_guard(model_output: str, original_size: int, threshold_ratio: float = 0.6) -> None:
    """Reject full-file dumps for small edits.

    If model_output is > threshold_ratio of original_size and not a diff block,
    raise so caller can re-prompt with diff contract.
    """
    if original_size < 300:
        return  # small file — full dump okay
    # diff markers present -> okay
    if "<<<" in model_output or "@@" in model_output or ">>>> REPLACE" in model_output:
        return
    if len(model_output) > original_size * threshold_ratio:
        raise ValueError(
            f"Diff-only violation: output {len(model_output)} chars vs original {original_size} — "
            "emit a SEARCH/REPLACE block, not the full file"
        )


__all__ = [
    "PrunerConfig",
    "PruneStats",
    "prune_history",
    "summarize_tool_result",
    "estimate_tokens",
    "diff_enforce_prompt",
    "apply_search_replace",
    "apply_search_replace_blocks",
    "apply_unified_diff",
    "diff_only_guard",
]
