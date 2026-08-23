"""Scoreboard rendering for benchmark results.

Plain-text tables — the scoreboard is data presentation, not UI chrome.
"""

from __future__ import annotations

from wisp.benchmark.runner import BenchResult
from wisp.benchmark.scoring import ModelScorecard


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - len(text))


def render_result_line(res: BenchResult, width: int = 72) -> str:
    """One line per (model, task) outcome, for live progress output."""
    marker = {"PASS": "✓", "FAIL": "✗", "TIMEOUT": "⏱"}.get(res.status(), "?")
    detail = res.verify_detail or res.error
    suffix = f" — {detail}" if detail and not res.passed else ""
    line = f"  {marker} {res.model} · {res.task_id}: {res.status()} ({res.duration_s:.1f}s){suffix}"
    if len(line) > width:
        return line[: width - 1] + "…"
    return line


def render_scoreboard(cards: list[ModelScorecard]) -> str:
    """Render the per-model summary table."""
    header = (
        _pad("MODEL", 28)
        + _pad("PASS", 7)
        + _pad("FAIL", 6)
        + _pad("TIMEOUT", 9)
        + _pad("RATE", 7)
        + "AVG s"
    )
    lines = [header, "-" * len(header)]
    for card in cards:
        avg = card.total_duration_s / card.total if card.total else 0.0
        lines.append(
            _pad(card.model[:27], 28)
            + _pad(f"{card.passed}/{card.total}", 7)
            + _pad(str(card.failed), 6)
            + _pad(str(card.timed_out), 9)
            + _pad(f"{card.pass_rate:.0%}", 7)
            + f"{avg:.1f}"
        )

    out = "\n".join(lines)

    best = _best_model(cards)
    if best is not None:
        out += f"\n\nBest: {best.model} ({best.pass_rate:.0%} pass)"
    return out


def _best_model(cards: list[ModelScorecard]) -> ModelScorecard | None:
    scored = [c for c in cards if c.total > 0]
    if not scored:
        return None
    # Pass rate first; ties broken by fewer timeouts, then speed.
    return sorted(
        scored,
        key=lambda c: (-c.pass_rate, c.timed_out, c.total_duration_s / c.total),
    )[0]


def render_full_report(results: list[BenchResult], models: list[str]) -> str:
    """Live progress lines followed by the summary table."""
    from wisp.benchmark.runner import aggregate

    lines = ["Benchmark results:", ""]
    for res in results:
        lines.append(render_result_line(res))
    lines.append("")
    lines.append(render_scoreboard(aggregate(models, results)))
    return "\n".join(lines)
