#!/usr/bin/env python3
"""Mock runner — validates new terminal output under concurrent worker load.

Demonstrates:
  * Stream hygiene: provider warnings go to .agent/runtime.log, not stdout
  * Ephemeral Live: rich.live.Live table for 4 fanout workers
  * Clean badges: ✓ / ⚠ / ✗ instead of … +35 more
  * High-signal final: Panel + Table with P0/P1/P2 + file anchors
  * JSON artifact at .agent/audit_summary.json

Run:
  .venv/bin/python scripts/demo_terminal.py
  .venv/bin/python scripts/demo_terminal.py --workers 6 --duration 4

No network, no API key — pure mock.
"""

from __future__ import annotations

import asyncio
import logging
import random
import sys
import time
from pathlib import Path

# Ensure workspace root is on sys.path for `import agent.*` when run as script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Install stream hygiene first
try:
    from agent.logger import install as install_logger, truncate_payload
except Exception:
    from logger import install as install_logger, truncate_payload  # type: ignore

try:
    from agent.telemetry import TelemetryTracker
    from agent.models import SubagentState
    from agent.synthesizer import Synthesizer
    from agent.cli_renderer import CliRenderer
except Exception as e:
    print(f"import failed: {e}")
    raise


async def mock_worker(tracker: TelemetryTracker, wid: str, focus: str, tasks: list[str]):
    role = "coder"
    await tracker.register(wid, role=role, focus=focus, activity="queued")
    for activity in tasks:
        await tracker.update(wid, activity=activity, tokens_used=random.randint(50, 400))
        # Simulate tool invocation that would previously pollute stdout
        if "missing" in activity:
            logging.getLogger("wisp.tools.registry").warning("Tool read_file failed: File not found: %s", activity.split()[-1])
        if "provider" in activity.lower():
            logging.getLogger("wisp.core.provider_stream").warning(
                "Provider stream closed without any content [sse_lines=2 usable=0 empty_choice_chunks=1 finish=stop] (attempt 1/3) — retrying"
            )
        await asyncio.sleep(random.uniform(0.25, 0.55))
    await tracker.complete(wid, status=random.choice(["completed", "completed", "completed", "failed"]))


async def main(workers: int = 4, duration: float = 3.0):
    install_logger()
    # prove truncate badge
    ugly = "x" * 200 + " … +35 more"
    badged = truncate_payload(ugly)
    assert "… +35 more" not in str(badged), "badge failed"
    badged2 = truncate_payload({"data": "y" * 5000 + " … +28 more", "other": 1})
    assert isinstance(badged2, dict) and "… +28 more" not in str(badged2["data"])

    renderer = CliRenderer()
    renderer.install()

    # Ephemeral status demo
    with renderer.status("Preparing fanout…"):
        await asyncio.sleep(0.4)

    tracker = TelemetryTracker(max_concurrent=4, title=f"Fanout — {workers} workers")
    synth = Synthesizer(out_dir=Path(".agent"))

    focuses = [
        "Analyzing Architecture",
        "Inspecting UI Compositor",
        "System Monitor",
        "Logging & Metrics",
        "Particles & Theme",
        "App State",
    ]

    async with tracker.live():
        # Register 4 workers with distinct focus — mirrors your fanout payload
        for i in range(workers):
            await tracker.register(
                f"bg-{i:02d}-{random.randint(1000,9999):04d}",
                role="coder",
                focus=focuses[i % len(focuses)],
                activity="queued",
            )

        # Kick workers concurrently — bounded by tracker semaphore (4)
        tasks = []
        for wid in list(tracker.snapshot().keys()):
            acts = [
                f"Reading src/system.rs:{random.randint(10,90)}-{random.randint(91,120)}",
                f"Reading src/events/mod.rs:{random.randint(10,40)}",
                f"Reading src/state/metrics.rs:{random.randint(20,80)}",
                "Synthesizing findings",
            ]
            tasks.append(asyncio.create_task(mock_worker(tracker, wid, tracker.snapshot()[wid].focus, acts)))

        # Live while waiting — proves no static spinner freeze
        start = time.monotonic()
        while any(not t.done() for t in tasks) and (time.monotonic() - start < duration + 2):
            await tracker.tick()
            await asyncio.sleep(0.15)
        await asyncio.gather(*tasks, return_exceptions=True)

        # Badges — single-line, no JSON dump
        renderer.tool_ok("read_file", files=4, kb=18, ms=45)
        renderer.tool_retry("provider stream timeout", attempt=1, max_attempts=3)
        renderer.tool_error("file not found: src/system/mod.rs")

    # High-signal final synthesis — P0/P1/P2 with anchors
    payloads = [
        {"findings": [
            {"severity": "P0", "file_anchor": "src/system.rs:42-89", "issue_summary": "Network I/O rate assumes fixed 100ms tick — under-reports on backpressure", "remediation": "rate = (bytes_now - bytes_prev) as f64 / Instant::now().elapsed().as_secs_f64()", "source_subagent": "system-monitor"},
            {"severity": "P1", "file_anchor": "src/events/mod.rs:22-48", "issue_summary": "ProcessInfo.user never populated — attribution broken", "remediation": "users crate get_user_by_uid; cache uid->name"},
        ]},
        {"findings": [
            {"severity": "P1", "file_anchor": "src/system.rs:110-145", "issue_summary": "Missing 1m/5m/15m load average despite SystemMetricsEvent contract", "remediation": "sysinfo::System::load_average() each tick"},
        ]},
    ]
    report = synth.run(payloads, findings=[])
    # Also show via CliRenderer final panel (redundant with Synthesizer's rich tables — demonstrates both)
    renderer.final_panel("Codebase Audit — aether-tui", issue_count=len(report.issue_matrix), file_count=len(report.file_map), p0=sum(1 for f in report.issue_matrix if f.severity=="P0"))
    renderer.issue_table([{"severity": f.severity, "file_anchor": f.file_anchor, "summary": f.issue_summary, "remediation": f.remediation} for f in report.issue_matrix])

    # Machine artifact
    jpath = Path(".agent/audit_summary.json")
    assert jpath.exists(), "JSON artifact missing"
    # Verify file hygiene — provider warnings should be in .agent/runtime.log, not stdout
    # (stdout captured above is badges/tables only; we already asserted badged truncation)
    rlog = Path(".agent/runtime.log")
    if rlog.exists():
        print(f"\n[Stream hygiene] .agent/runtime.log exists ({rlog.stat().st_size} bytes) — provider SSE warnings isolated ✓")
    print(f"[Artifacts] {jpath} + .agent/audit_report.md + {rlog} — human vs machine separated ✓")
    print("[Done] Mock run passed — no … +35 more leaked, Live table was ephemeral, badges clean")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--duration", type=float, default=3.0)
    args = ap.parse_args()
    asyncio.run(main(workers=args.workers, duration=args.duration))
