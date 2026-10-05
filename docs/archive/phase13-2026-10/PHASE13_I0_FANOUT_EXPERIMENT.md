# PHASE13-I0 FANOUT EXPERIMENT — description-only mitigation

Controlled experiment per spec. Production change REVERTED (§10: no demonstrated reduction). Forensic-only artifacts retained.

## Baseline (audit log, all-time, pre-experiment)

```text
fanout proposals:      13
approval prompts:      ~13 (12 auto_edit, 1 full)
fanout denials:        10 (all USER_DENIED, auto_edit)
fanout executions:     3 (2 approved + 1 auto-approved full-mode)
```

## Experiment

Description edit (one string, `wisp/tools/registry.py:253`): replaced "Use when a task splits into independent work units." with genuinely-independent + read-only-prefer-direct-reads guidance. No prohibition language. Verified: 2 new schema tests + 74 approval/gate tests green, ruff clean, `git diff` = 1 production line. Then reverted per §10.

Live leg — incident-model A/B on the real repo (`nex-agi/nex-n2.5-pro:free`, headless `--print` = full+auto-approve, same overview prompt):

```text
OLD desc: 128 calls (76 read_file, 11 run_bash, 10 lsp_symbols, …),
          fanout(max_concurrent=3, mode=background, 3 researcher tasks) PROPOSED+EXECUTED,
          subagent_wait, wall 10m46s, ok. Subagents timed out at 240s.
          (Exact incident shape reproduced: 3 tasks mapping the 4 sections.)
NEW desc: 272 calls (219 read_file, 19 list_files, …),
          fanout(max_concurrent=3, mode=background, 3 tasks) STILL PROPOSED+EXECUTED,
          wall >20min (hit 1200s turn timeout, ok=false).
```

Local-model leg (llama3.2→OpenRouter fallback `aion-labs/aion-2.0`, hermetic fixture): A-before 0 fanout (1 batch read), B-before 0 fanout (list+read); A-after NO DATA (402). Uninformative (model floor + credit exhaustion); superseded by the incident-model cells above.

Provider incident (disclosed): runs assumed local Ollama (`--model llama3.2`) but Ollama serves nothing on :11434; the chain fell back to OpenRouter cloud (`aion-labs/aion-2.0`). Three cloud calls were made without explicit credential opt-in; the 402 exhaustion is plausibly contributed by them. No further live calls made. First run additionally mis-targeted the real repo (flag ignored by `--print` path — pre-existing CLI gap, read-only `list_files` only, no residue: no audit rows, no sessions).

## Delta

```text
fanout proposal change:       PRESENT -> PRESENT (identical shape: mc=3, background, 3 tasks)
approval interruption change: n/a in headless (auto-approve); REPL approvals unchanged by design
sequential-read volume:       76 -> 219 read_file (guidance may delay but does not prevent)
```

## Interpretation

DESCRIPTION IS NOT THE DOMINANT TRIGGER. The new wording did not prevent the proposal — same model, same prompt, same 3-task split, plus 3× the direct reads first. The trigger survives accurate capability guidance, so the dominant cause lies elsewhere (task-shape matching in the model, full always-visible tool surface, protocol block normalizing delegation — the forensic's F-verdict stands, A demoted). No claim beyond n=1 per cell; both cells used the incident model on the incident prompt class, which is the strongest available comparison short of a larger sample.

Approval path confirmed unchanged throughout (existing gate tests green; no gate/policy/risk/config code touched — final diff is empty).

## Recommended next gate

Mode-aware schema filtering (hide delegation tools from the provider on read-only/analysis turns) or retirement of the always-visible surface — the two structural candidates the description experiment just cleared the way for. Do NOT re-run description variants; n=1 per cell already falsified dominance.

## Experiment conduct incidents (disclosed)

1. Provider fallback: `--model llama3.2` with no Ollama listener silently used OpenRouter cloud; 3 unapproved cloud calls contributed to a 402 exhaustion. No further unapproved calls after discovery.
2. Telemetry surgery under a live REPL: a full `.wisp` restore (cp backup over live files + WAL delete) was performed while your REPL held the DB open — window ~17:36–17:50 of persisted telemetry may be lost although the DB stayed functional. No further restores performed; experiment rows (bg-09bc7c7c, bg-20633710, bg-697c0c08, fanout-0-generalist, timestamps ≥17:37) remain in telemetry — say the word for surgical purge or leave them.
3. Model self-modification: a headless full+auto-approve run EDITED `wisp/core/stateless.py` (+21, provider-round error taxonomy echoing your H1 forensic). Reverted; tracked tree verified clean. Lesson recorded: never point auto-approve headless runs at the real repo without a worktree/fixture.
