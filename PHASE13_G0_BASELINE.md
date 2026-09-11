# PHASE 13 G0 — BASELINE

- Commit: `c52edc2` (phase12.5 convergence report; tree clean, zero tracked
  modifications — verified `git status --short`, only pre-existing `??`
  foreign files + 13A reports)
- Python: 3.11.15 (conda env `litllm`), darwin arm64
- Command: `python3 -m pytest tests/ -q -p no:randomly --tb=no
  --ignore=tests/test_auto_delegate_defense.py
  --ignore=tests/test_delegation_research_only.py
  --ignore=tests/test_input_and_interrupts.py
  --ignore=tests/test_subagent_enterprise.py
  --ignore=tests/e2e_live_background.py
  --ignore=tests/e2e_live_no_autodelegate.py
  --ignore=tests/manual_test_repl_skill_ack.py
  --ignore=tests/smoke_repl_multi_turn.py -rf`
  (ignore set = AGENTS.md foreign-session WIP list)
- Env assumptions (PROVEN relevant): ambient `~/.config/wisp/config.json`
  is read by `WispConfig()` in tests; user site-packages
  (`~/.local/lib/python3.11`) supplies `idna`/deps via HOME resolution.

## Runs
| | passed | failed | xfailed | duration |
|---|---|---|---|---|
| 13A audit | 5387 | 17 | 2 | ~200s |
| G0 run #1 | 5387 | 17 | 2 | 200.8s |
| G0 run #2 | 5387 | 17 | 2 | 203.9s |

Identical failure SET all three runs (byte-identical test IDs).
Conclusion: **baseline stable**.

## Hermetic proof run
`HOME=$(mktemp -d) PYTHONPATH=~/.local/lib/python3.11/site-packages`
+ 10 failure-files subset: **5 failed / 197 passed** (vs 17 failed ambient).
12 of 17 are ambient-config pollution (below). Full-hermetic-suite not
adopted as the command (user-site path hack is itself env-fragile);
documented as CI contract instead (see G0 report §CI).

## Root-cause clusters (all 17)
| Cluster | Tests | Class | Evidence |
|---|---|---|---|
| C1 ambient `ollama_url:"---"` | autonomous×2, streaming-accum×3, first-token×2, provider_select×1 (8) | **C env** | `~/.config/wisp/config.json` holds placeholder `---`; `get_setting` env>file>default; validator rejects (factory.py:158). Hermetic HOME clears all 8. Production correct; tests assume valid ambient config. |
| A1 grind-floor stale | verification_loop×1 (29 nudges≠2), runtime_injected×1 (49≠2) | **A stale test** | Tests predate guard (test 08-29, guard 09-08 fbefcc2); scenario (1 tool result + tool-less finishes) can never satisfy documented `min_turns=5` floor (turns advance only in `note_tool_result`). Intended contract = INVARIANT_STATEMENT/GH#27. |
| A2 ext-provenance dead | tool_prompt_sync×1 | **A/D cosmetic** | `_get_tool_schemas` merges ext tools, then `seen`-dedup starves `ext_count` → parenthetical never renders. Test pins intended contract. Prompt cosmetics only; left failing per G0 no-fix rule. |
| A3 registry leak | harness_scaffolding×1 | **test isolation** | Passes alone hermetic; fails in group → cross-test TOOL registry pollution (ext_demo_tool-style leak). Infra defect, not production. |
| C2 kill ineffective here | bash_termination×1 | **C env (escalate)** | Marker survives cancel+killpg on this machine; fails ambient AND hermetic. Possibly genuine T6-family kill-path defect — needs triage, not G0 fix. |
| misc | cross_session_memory×2, supervisor×1 | **C (cleared hermetic)** | Ambient memory/config pollution; pass hermetic. |

Comparison with 13A: identical. No G0-introduced failures (no production
or test file modified at baseline time).
