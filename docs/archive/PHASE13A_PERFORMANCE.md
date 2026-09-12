# PHASE 13A — PERFORMANCE

Machine: dev Mac, CPython 3.11, single run each unless noted.
Method: /tmp/h13a_perf.py (outside tree, mock runners, tempdirs).

## Measured (TESTED)
| Workload | Result |
|---|---|
| `import wisp.__main__` | 142–379ms (run variance; cold import, no lazy deficit) |
| workspace apply 10 files | ~5ms, ok=True |
| workspace apply 50 files | ~28ms, ok=True |
| workspace apply 100 files (= MAX_CS_FILES cap) | ~81–87ms, ok=True |
| graph chain 10 / 50 / 200 (mock runner) | 11.5 / 41.8 / 134.2ms |
| graph fanout 16 / 64 / 256 | 5.8 / 19.1 / 88.5ms |
| validator 512-fan | 35.6ms (census earlier: 67–79ms — same order) |
| 100× session save (UnifiedStore) | 5.4ms total (~54µs/write) |
| containment resolve 2000× | 45–48ms (~23µs/call, no regression vs 12.5) |
| audit write | ~40µs (12.5 figure, unchanged code path shape) |
| 16 threads × 20 session writes | 0.02s, zero errors |
| 16 concurrent 2-node graphs, shared store | 16/16 succeeded |
| 16×25 concurrent JSONL audit writes | 400/400 present, chain FORKED (verify_bad=3) |
| concurrent first-touch graph DB init | 1/10 trials `database is locked` |

## Analysis (INFERRED from numbers)
- Workspace apply is superlinear (~0.5/0.55/0.85 ms/file at 10/50/100):
  per-op re-journal + full-scope re-hash. Single changeset capped at 100
  files / 1MB per file (MAX_CS_FILES/MAX_FILE_BYTES) — the cap IS the
  scaling control. 10K-file workspace ops = 100 changesets minimum.
- Graph overhead ≈ 0.6ms/node; validator fine to 512-fan.
- SQLite single-process ceiling not hit at these sizes; contention appears
  first at init/DDL (H3) and chain-RMW, not steady-state writes.

## Scale projection (§32)
- 100K LOC workspace: RepoMap/context assembly (6K-token cap truncates —
  quality, not perf); changeset cap forces chunked applies; fine.
- 250K: first bottleneck = workspace apply journal-per-op fsync +
  full-scope hash on every changeset; second = single-file SQLite WAL
  under server+CLI concurrent writers (no app retry; 5–10s busy waits
  then loud failure).
- 500K–1M: hard ceiling = in-memory session maps (1000-entry evictions),
  unbounded event/trace tables (no retention), thread-local conns never
  closed. Sharding needed (distant — matches census forecast).
- 10/50/100 concurrent runs: init race + `transition()` seq duplication
  bite before throughput does. No pooling anywhere (each provider owns its
  session) — socket count grows with concurrency, unmeasured (UNKNOWN).

## Not measured (UNKNOWN, proposed gates)
- 10/30/60-min soak (RSS/fds/task growth) — no monotonic-growth data.
- Provider-degradation timing (no network fault injection performed).
- 1K-run persistence sizing; concurrent-run throughput curve.
