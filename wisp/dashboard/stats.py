"""Pure statistics over benchmark rows. No I/O, no clock.

A row is one judged attempt: {"task", "verdict", "final", "model", "label", "seconds", "claim_honest", ...}. Only `final` rows are outcomes; the earlier
attempts of a task that was retried because of infrastructure trouble (a 429, a timeout) are kept for the infra rate and never scored.
A task the agent could not be asked (INFRA) or that was broken before any work (BROKEN) is not a failure of the agent, so it is excluded from the pass
rate and reported next to it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Callable, Iterable

UNSCORED = frozenset({"INFRA", "BROKEN"})
VERDICTS = ("SOLVED", "FAILED", "NO-OP", "GAMED", "INFRA", "BROKEN")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n trials; None when n is 0 (there is no estimate to bound)."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe_difference(k1: int, n1: int, k2: int, n2: int, z: float = 1.96) -> tuple[float, float, float] | None:
    """Difference of two proportions p1 - p2 with a Newcombe-Wilson interval; (diff, lo, hi), or None when either side has no trials."""
    a, b = wilson(k1, n1, z), wilson(k2, n2, z)
    if a is None or b is None:
        return None
    p1, p2 = k1 / n1, k2 / n2
    lo = (p1 - p2) - math.sqrt((p1 - a[0]) ** 2 + (b[1] - p2) ** 2)
    hi = (p1 - p2) + math.sqrt((a[1] - p1) ** 2 + (p2 - b[0]) ** 2)
    return p1 - p2, lo, hi


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, int(math.ceil(q * len(sorted_values))) - 1))
    return sorted_values[idx]


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Counts, pass rate with its interval, honesty and timing for a set of rows (attempts included, outcomes from the `final` ones)."""
    rows = list(rows)
    finals = [r for r in rows if r.get("final", True)]
    counts = {v: 0 for v in VERDICTS}
    for r in finals:
        counts[str(r.get("verdict"))] = counts.get(str(r.get("verdict")), 0) + 1
    scored = [r for r in finals if r.get("verdict") not in UNSCORED]
    solved = counts["SOLVED"]
    seconds = sorted(float(r["seconds"]) for r in scored if isinstance(r.get("seconds"), (int, float)))
    dishonest = sum(1 for r in finals if r.get("claim_honest") is False)
    # The judge calls any mismatch between the agent's own success flag and the truth "dishonest". They are different failures: claiming success on work that is
    # not solved is the dangerous one; saying "not ok" on work that is solved is cautious (and is often a run cut short at the end by a provider error).
    overclaims = sum(1 for r in scored if r.get("claim") is True and r.get("verdict") != "SOLVED")
    underclaims = sum(1 for r in scored if r.get("claim") is False and r.get("verdict") == "SOLVED")
    interval = wilson(solved, len(scored))
    attempts = len(rows)
    infra_attempts = sum(1 for r in rows if r.get("verdict") == "INFRA")
    return {
        "outcomes": len(finals), "attempts": attempts, "scored": len(scored), "solved": solved,
        "pass_rate": (solved / len(scored)) if scored else None,
        "ci_low": interval[0] if interval else None, "ci_high": interval[1] if interval else None,
        "counts": counts, "dishonest": dishonest, "overclaims": overclaims, "underclaims": underclaims,
        "infra_attempts": infra_attempts, "infra_rate": (infra_attempts / attempts) if attempts else None,
        "median_s": _percentile(seconds, 0.5), "p90_s": _percentile(seconds, 0.9),
    }


def group_by(rows: Iterable[dict[str, Any]], key: Callable[[dict[str, Any]], Any]) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return dict(out)


def task_matrix(rows: Iterable[dict[str, Any]], group_key: Callable[[dict[str, Any]], str]) -> dict[str, dict[str, dict[str, int]]]:
    """task -> group -> {solved, scored, infra, outcomes}: which tasks each configuration solves, and where the noise is."""
    matrix: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    for r in rows:
        if not r.get("final", True):
            continue
        cell = matrix[str(r.get("task"))].setdefault(group_key(r), {"solved": 0, "scored": 0, "infra": 0, "outcomes": 0})
        cell["outcomes"] += 1
        if r.get("verdict") in UNSCORED:
            cell["infra"] += 1
        else:
            cell["scored"] += 1
            cell["solved"] += 1 if r.get("verdict") == "SOLVED" else 0
    return {t: dict(g) for t, g in matrix.items()}


def verdict_label(summary: dict[str, Any]) -> str:
    """One honest sentence about how much a summary can be trusted; the dashboard shows it next to every pass rate."""
    n = summary["scored"]
    if n == 0:
        return "no scored runs yet"
    if n < 10:
        return f"only {n} scored runs: the interval is wide, treat the rate as a hint"
    if summary["infra_rate"] and summary["infra_rate"] > 0.3:
        return f"{round(summary['infra_rate'] * 100)}% of attempts hit infrastructure trouble: the scored runs are a biased sample"
    return f"{n} scored runs"
