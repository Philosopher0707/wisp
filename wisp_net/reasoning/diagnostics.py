"""Predictive diagnostics: optic degradation forecasts and error/power correlation.

Deterministic analysis the predictive-diagnostics agent reasons over, computed from the
metric store. A forecast is an ordinary least-squares line through receive power; it
reports its fit (R²) so a trend on noise is not mistaken for a trend.
"""

from __future__ import annotations

import math
from typing import Any

from wisp_net.sim.network import RX_FCS_ONSET_DBM, RX_LOS_DBM
from wisp_net.state.tsdb import MetricStore

MIN_POINTS = 6
MIN_R2 = 0.8


def _fit(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """slope, intercept, r² of y over x."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    if sxx == 0:
        return 0.0, my, 0.0
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((p[1] - my) ** 2 for p in points)
    ss_res = sum((p[1] - (slope * p[0] + intercept)) ** 2 for p in points)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return slope, intercept, r2


def _minutes_to(threshold: float, now_value: float, slope_per_s: float) -> float | None:
    if now_value <= threshold:
        return 0.0
    if slope_per_s >= 0:
        return None
    return round((threshold - now_value) / slope_per_s / 60.0, 1)


def optics_forecast(tsdb: MetricStore, now: float, device: str | None = None,
                    window_s: float = 1800.0) -> list[dict[str, Any]]:
    """Every optic whose receive power is falling measurably, worst first."""
    pattern = f"{device or '*'}|if.rx_power_dbm|*"
    rows = []
    for series, points in tsdb.query(pattern, now - window_s, now).items():
        if len(points) < MIN_POINTS:
            continue
        slope, _, r2 = _fit([(p.ts - now, p.value) for p in points])
        current = points[-1].value
        dev, _, port = series.split("|")
        falling = slope < 0 and r2 >= MIN_R2
        if not falling and current > RX_FCS_ONSET_DBM + 2:
            continue
        to_errors = _minutes_to(RX_FCS_ONSET_DBM, current, slope) if falling or current <= RX_FCS_ONSET_DBM else None
        to_los = _minutes_to(RX_LOS_DBM, current, slope) if falling or current <= RX_LOS_DBM else None
        risk = ("critical" if to_los is not None and to_los <= 60 else
                "high" if to_errors is not None and to_errors <= 60 else
                "medium" if falling else "low")
        rows.append({"port": f"{dev}:{port}", "rx_power_dbm": round(current, 2),
                     "slope_db_per_hour": round(slope * 3600, 2), "fit_r2": round(r2, 3),
                     "minutes_to_fcs_errors": to_errors, "minutes_to_loss_of_signal": to_los, "risk": risk,
                     "thresholds_dbm": {"fcs_error_onset": RX_FCS_ONSET_DBM, "loss_of_signal": RX_LOS_DBM}})
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(rows, key=lambda r: (order[r["risk"]], r["minutes_to_loss_of_signal"] or math.inf))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return 0.0 if sx == 0 or sy == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def error_correlation(tsdb: MetricStore, now: float, device: str, port: str,
                      window_s: float = 1800.0) -> dict[str, Any]:
    """Do a port's FCS errors follow its receive power?

    The relation is a threshold and then a steep rise (no errors until about -12 dBm), so a
    linear (Pearson) coefficient understates it; Spearman's rank correlation measures the
    monotone association. The physical signature is checked too: errors only below the
    onset threshold point at the optic.
    """
    errors = tsdb.query(f"{device}|if.fcs_per_s|{port}", now - window_s, now).get(f"{device}|if.fcs_per_s|{port}", [])
    power = tsdb.query(f"{device}|if.rx_power_dbm|{port}", now - window_s, now).get(
        f"{device}|if.rx_power_dbm|{port}", [])
    by_ts = {p.ts: p.value for p in power}
    pairs = [(by_ts[e.ts], e.value) for e in errors if e.ts in by_ts]
    base: dict[str, Any] = {"port": f"{device}:{port}", "samples": len(pairs)}
    if len(pairs) < MIN_POINTS:
        return {**base, "verdict": "insufficient data", "spearman_rho": None}
    erroring = [pw for pw, err in pairs if err > 0]
    if not erroring:
        return {**base, "verdict": "no FCS errors in the window", "spearman_rho": None}
    rho = _pearson(_ranks([p for p, _ in pairs]), _ranks([e for _, e in pairs]))
    below = [(pw, err) for pw, err in pairs if pw <= RX_FCS_ONSET_DBM + 1.0]
    below_onset = all(pw <= RX_FCS_ONSET_DBM + 1.0 for pw in erroring)
    rate_below = sum(1 for _, err in below if err > 0) / len(below) if below else 0.0
    rho_below = (_pearson(_ranks([p for p, _ in below]), _ranks([e for _, e in below]))
                 if len(below) >= 3 and len({p for p, _ in below}) > 1 else None)
    steady_power = max(p for p, _ in pairs) - min(p for p, _ in pairs) < 0.5
    if below_onset and rate_below >= 0.5 and (rho_below is None or rho_below <= -0.5):
        verdict = ("physical layer, optics: errors appear only below the error-onset power and grow as receive "
                   "power falls")
    elif below_onset:
        verdict = ("consistent with optics but not yet conclusive: errors appear only below the error-onset power, "
                   "still sparsely; check again in a few minutes")
    elif steady_power:
        verdict = "not optics: receive power is steady while errors occur (port, cable damage or far-end transmitter)"
    else:
        verdict = "not explained by receive power: check the port, the cable and the far-end transmitter"
    return {**base, "spearman_rho": round(rho, 3), "errors_only_below_onset": below_onset,
            "spearman_rho_below_onset": None if rho_below is None else round(rho_below, 3),
            "error_rate_below_onset": round(rate_below, 2), "verdict": verdict,
            "rx_power_dbm_range": [round(min(p for p, _ in pairs), 2), round(max(p for p, _ in pairs), 2)],
            "fcs_per_s_max": round(max(e for _, e in pairs), 1)}
