# Graph Planner Evaluation (Phase 9I)

Corpus: `scripts/eval_planner.py` — 6 deterministic cases (no model calls;
canned IRs through compile + quality + policy).

| Case | Shape | Result |
|---|---|---|
| trivial-typo | SINGLE_AGENT, 1 node | valid, score 100 |
| single-file-bug | chain with mappings | valid, score 85 (advisory: add verifier) |
| multimodule-fan | split → 2 branches → join | valid, score 70 (advisory: add mappings + verifier) |
| security-change | impl → verifier → done (accept) | valid, score 100 |
| fake-serial | 4-node chain, no dataflow | **flagged OPT-001** |
| no-verifier | 3 agents, no verifier | **flagged OPT-002** |

Validity: 6/6 compile clean under policy. Dependency quality: mappings
present where data flows; fake serialization detected. Parallelism: fan
accepted with explicit join. No unnecessary fanout in corpus (trivial stays
single-node). Failure cases: planner-bound `function:` gates and implicit
`'all'` tools are REJECTED (verified during corpus construction — the
pipeline refuses what planners may not decide).

Optimization opportunities for Phase 10: OPT-001 (collapse fake
serialization), OPT-002 (insert verifier), OPT-003 (artifact-ize large
transfers), OPT-004 (cap fanout/size). Not implemented per scope.
