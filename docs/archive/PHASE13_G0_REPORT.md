# PHASE 13 G0 — REPORT

## Executive Summary
- Baseline: stable. `c52edc2`, 5387 passed / 17 failed / 2 xfailed across
  13A + 2 G0 runs (byte-identical failure set). See PHASE13_G0_BASELINE.md.
- CI: 17 failures classified (12 ambient-config pollution PROVEN hermetic;
  5 residual: 2 stale-test, 1 cosmetic, 1 test-isolation, 1 env-escalate).
  Zero remediation applied (no safe change under G0 rules) — documented
  contract instead. No green manufactured.
- Soak: 10m A (2765 ops) + 10m B (225 ops, 225/225/225 retry/timeout/
  cancel) runnable via one command; 30m/60m same binary (runnable, 10m
  evidence). One monotonic leak found: fds +55 (A) / +153 (B), slope>0 —
  G0-NEW-1 (P2, measures 13A P2-8). All else bounded.
- Kill points: 4/4 SIGKILL harnesses green with machine-readable records;
  no false success anywhere. Residue finding: torn per-op journal blinds
  forward recovery (G0-NEW-2, P1 candidate for G1).
- Network faults: 13 fault cases green; trailing-mid-stream error yields
  `content, done` with NO error event (G0-NEW-3, P1, deterministic G1
  repro — upgrades 13A B4-2 to TESTED).
- Multiprocess: 8×25 intact once, forked on rerun (contention-dependent);
  sustained 8×2s forked at entry 442 (G1 repro for P1-4). SQLite init 8/8
  ok here (13A 1/10 thread-race stands).
- Replay: g0.1 spec + 9 fixtures green; secrets rejected; deterministic
  replay explicitly NOT claimed.

## Baseline
Commit `c52edc2`, Python 3.11.15, darwin arm64. Command + ignore set in
PHASE13_G0_BASELINE.md. Runs: 13A 5387/17/2 (~200s); G0#1 5387/17/2
(200.8s); G0#2 5387/17/2 (203.9s). Post-harness: 5416–5417 passed + same
17 (+1 transient smoke assertion bug, mine, fixed). Conclusion: stable.

## CI
| Test | Classification | Reproduces | Remediated | Why |
|---|---|---|---|---|
| 8× Ollama-URL cluster | C env | ambient only; hermetic clears | NO | `~/.config/wisp/config.json` placeholder `---`; production validator correct; tests assume valid ambient config |
| verification_loop, runtime_injected | A stale test | always | NO | predate guard; scenario can't satisfy documented `min_turns=5`; G0 must not redesign guard or bless it via test edits |
| tool_prompt_sync | A/D cosmetic | always | NO | `seen`-dedup starves ext counter; prompt cosmetics; production fix = behavior-adjacent, out of G0 |
| harness_scaffolding | test isolation | in-group only | NO | passes alone; cross-test registry pollution; infra defect |
| bash_termination | C env, ESCALATE | always here | NO | kill ineffective on this machine; possibly genuine T6 kill-path defect — needs triage |
| cross_session_memory×2, supervisor×1 | C env | ambient only | NO | cleared hermetic |

CI contract: full command (above) with 17 known failures + hermetic-HOME
variant with 5 known failures. Harness suites (`tests/reliability/`) must
be fully green — they are (31 tests).

## Soak
| Workload | Duration | RSS Δ | FDs Δ | Threads Δ | Tasks Δ | Sockets Δ | DB Δ | Result |
|---|---|---|---|---|---|---|---|---|
| A steady | 90s smoke | +16MB | +41 slope+1.7 | 0 | 0 | 0 | +2.2MB | FAIL (fds) |
| B failure-heavy | 90s smoke | small | +slope>0 | 0 | 0 | 0 | ok/op | FAIL (fds) |
| A steady | 10m | +19.8MB | +55 slope+1.8 | 0 | 0 | 0 | +10.4MB (3.8KB/op) | FAIL (fds) |
| B failure-heavy | 10m | −5.8MB | +153 slope+6.3 | 0 | 0 | 0 | +2.9MB | FAIL (fds) |

Thresholds (soak.py REPORT_THRESHOLDS): threads≤+4, fds≤+32 OR
second-half-slope≤0, sockets≤+8, children=0, tasks±2, rss≤100MB×scale OR
slope≤0, db/op≤64KB. Rule honors warmup (slope gate) and still caught the
real leak. 30m/60m: same command (`--duration 30m/60m`), runnable, not
executed (10m per-op rates stable; hour-long runs add no new signal class).

## Kill Points
| Subsystem | Kill Point | Recovery | False Success | Duplicate Effect | Data Loss |
|---|---|---|---|---|---|
| graph | run-created, SIGKILL | orphan `queued`, unclaimed | NO | no | no |
| graph | mid-run (node RUNNING), SIGKILL | resume→`succeeded` (legit re-execution) | NO | UNKNOWN (at-least-once) | no |
| workspace | mid-apply (journal+2/60 landed), SIGKILL | `rolled-back:1`, journal drained, 2 files residue | NO | no | no (residue, see G0-NEW-2) |
| persistence | mid-transition-loop, SIGKILL | list_ok, 331 runs, dup_seq=0 (namespaced) | NO | none observed | no |

Records: `killpoints.jsonl` (kill_point/operation/process_exit/
recovery_result/final_state/artifacts_consistent/workspace_consistent/
audit_consistent/false_success/duplicate_effect/data_loss/notes).

## Network Faults
| Fault | Requests | Retries | Final State | Partial Output | Result |
|---|---|---|---|---|---|
| 429 always | 3 | 2 | returned-429 | no | observed, attempts pinned |
| 500→ok | 2 | 1 | returned-200 | no | observed |
| 503 always | 3 | 2 | returned-503 | no | observed |
| 400 | 1 | 0 | returned-400 | no | observed, no-retry pinned |
| reset→ok | 2 | 1 | returned-200 | no | observed |
| timeout always | 3 | 2 | raised-Timeout | no | observed |
| truncated (no complete) | 1 | 0 | done | YES retained | observed (P1-3 repro) |
| mid-stream conn-error | 1 | 0 | done, NO error event | YES retained | G0-NEW-3 (P1) |
| duplicate chunk | — | 0 | done | YES (no dedup) | observed for G1 |
| delayed 2s | — | 0 | done | — | clean < deadlines |
| malformed chunk | — | 0 | recorded | — | observed |
| never-ending | — | 0 | harness-timeout | no | producer cancel path observed |
| persistent empty turn | — | guard retries | turn-returned | — | amplification proxy (wall) |

Transport attempt counts pin current behavior (max_attempts honored,
400 not retried). Turn-level retry amplification via MockProvider turns:
guard empty-retry path exercised; per-seam counts in records.

## Multiprocess Concurrency
- Audit 8×25: 200/200, intact once; forked (bad=22) on rerun —
  contention-dependent, both recorded. Sustained 8×2s: 175020 written,
  fork at 442. G1 repro secured (flip `assert bad is None` post-P1-4).
- SQLite init 8 procs same fresh dir: 8/8 ok here. 13A thread-level 1/10
  `database is locked` stands (different contention shape).
- Records: `mp_concurrency.jsonl`. No loss in any run (appends hold;
  integrity doesn't).

## Replay
Captured: request, graph def+hash, policy fp, provider/model/config,
execution config, attempts/retries/timeouts/cancels (when present),
artifact refs+hashes, snapshot/changesets/journal disposition, audit head,
decisions/verdicts, timestamps, truncation state (schema-ready).
NOT captured: raw stream bytes, live budget counters, in-memory attempt
counters, pre-crash memory facts, env values/secrets (rejected by scan).
Deterministic: nothing claimed — temperature>0, unseeded retries,
wall-clock timeouts, or truncation≠complete force `deterministic:false`.
Nondeterministic remainder: provider sampling, timing, thread/process
interleaving, ambient machine state.

## Findings (new; 13A findings unchanged)
- G0-NEW-1 (P2): monotonic fd retention ~0.02/op steady, ~0.7/op
  failure-heavy (second-half slope>0 at 10m). Measures 13A P2-8. Gate: G3.
- G0-NEW-2 (P1 candidate): torn per-op journal (`"w"` truncate, no
  tmp+rename) → `recover()` drops it as corrupt → permanent partial apply
  (2/60 files, journal gone, future recover sees clean) + "rolled-back"
  disposition that rolls back nothing. Contradicts recover() docstring
  ("always before the first replace"). Deterministic repro:
  test_kp_workspace_midapply_then_killed. Gate: G1/G2.
- G0-NEW-3 (P1): mid-stream transport error after partial output →
  events `(content, done)`, zero error/status signal. Deterministic repro:
  test_stream_mid_error. Upgrades 13A B4-2 (INFERRED→TESTED). Gate: G1.

## Gate Decision
**G0 PASS WITH KNOWN FAILURES**

Why not PASS: 17 classified baseline failures remain (no remediation
permitted under G0 rules without manufacturing green); soak FAILs on the
real fd leak (threshold honesty working as designed); three new findings
need G1+ gates. Why not BLOCKED: every acceptance mechanism exists and is
green-or-honest — baseline reproduced ×3, harness suites 31/31, SIGKILL
recovery observed on 4 boundaries, faults injectable, amplification
measurable, multiprocess contention reproduced, replay schema validated,
zero production drift (`wisp/` untouched, verified by diff).

## Commands
```bash
# ordinary suite (17 known failures, see BASELINE doc)
python3 -m pytest tests/ -q -p no:randomly --tb=no [AGENTS.md ignores] -rf
# hermetic variant (5 known failures)
HOME=$(mktemp -d) PYTHONPATH=~/.local/lib/python3.11/site-packages python3 -m pytest ...
# G0 reliability suites (must be green)
python3 -m pytest tests/reliability/ -q -p no:randomly
# 10/30/60-minute soak (A or B)
python -m tests.reliability.soak --duration 10m --workload A --out /tmp/soakA
python -m tests.reliability.soak --duration 30m --workload B --out /tmp/soakB
python -m tests.reliability.soak --duration 60m --workload B --out /tmp/soakC
# kill-points / provider faults / multiprocess (also via full reliability run)
G0_KILL_OUT=/tmp/k pytest tests/reliability/test_killpoints.py -q
G0_PF_OUT=/tmp/pf pytest tests/reliability/test_provider_faults.py -q
G0_MP_OUT=/tmp/mp pytest tests/reliability/test_mp_concurrency.py -q
```
