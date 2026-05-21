---
name: qa
description: >
  Run QA tests for Wisp -- local Ollama-powered coding agent.
  Analyzes git diff to determine affected CLI areas, runs configured
  test flows, and generates a diff-targeted QA report.
  Uses tuistory for interactive CLI/REPL testing.
  Use when testing PRs, releases, or smoke testing the CLI.
---

# QA Orchestrator

**SCOPE: This skill performs manual/functional QA only -- verifying that the CLI REPL and core engine work by interacting with it as a real user would (terminal commands, REPL interaction). Do NOT run CI checks, linting, ESLint, typecheck, unit tests, or any static analysis. Those are handled by separate workflows.**

## Step 1: Load Configuration

Read `.factory/skills/qa/config.yaml` for environment URLs, credentials, personas, and app definitions.

## Step 2: Determine Target Environment

Use the default_target from config (`local`) unless the user specifies a different environment.

## Step 3: Analyze Git Diff

Run `git diff origin/main..HEAD --name-only` to determine what changed. Map changed files to apps using the path_patterns in config.yaml.

Files that don't match ANY app's path_patterns (e.g., `.factory/skills/**`, `docs/**`, `.github/**`, `tests/**`, config files) are NOT associated with any app. Do NOT run app test flows for them.

For the affected `wisp-cli` app:

- Read its sub-skill from `.factory/skills/qa-cli/SKILL.md`
- Run ONLY the flows relevant to the changed files:
  - `wisp/core/engine.py` changes → test single-shot and REPL tool result handling
  - `wisp/transport/cli_v2.py` changes → test REPL rendering, slash commands, session display
  - `wisp/commands.py` changes → test ALL slash commands that were modified
  - `wisp/entry.py` changes → test CLI startup, REPL initialization, headless mode
  - `wisp/config.py` changes → test `wisp config` commands, flag parsing
  - `wisp/tools/*.py` changes → test relevant tool execution in REPL
  - `wisp/server/**` changes → test `wisp server` start/stop
  - `wisp/provider*` changes → test `wisp check`, `wisp models`
  - `wisp/adapters.py`, `wisp/core/runtime.py` changes → test session management
  - `pyproject.toml`, `setup.py` changes → test `wisp --version`, `pip install -e .`

If NO app is affected by the diff, report INCONCLUSIVE: "No app code changed -- QA not applicable for this diff."

## Step 4: Pre-flight Checks

Before running any tests:

1. **Verify build**: Run `pip install -e .` in the repo root. If this fails, report BLOCKED.
2. **Verify CLI binary available**: Run `wisp --version` or `python -m wisp --version`. If missing, report BLOCKED.
3. **Verify Python tests pass**: Run `pytest tests/ -q --tb=no` to ensure no catastrophic regressions. Do NOT use this as the QA signal -- it's just a smoke test.
4. **Ollama check**: Run `wisp check` to verify Ollama is available. If not available, note it in the report but continue with MockProvider where applicable.

**TUI preflight**: If testing the TUI (`wisp tui --ink`), verify Node.js is available and the TUI is built (`wisp-tui/dist/wisp-tui.mjs` exists). If not built, run `cd wisp-tui && npm run build`.

## Step 5: Execute Diff-Relevant Flows Only

Read the sub-skill from `.factory/skills/qa-cli/SKILL.md`.

The sub-skill contains a MENU of available test flows. You must:

1. Read the diff carefully and identify which flows are relevant
2. Run those flows PLUS any adjacent flows that verify the change integrates correctly (e.g., if a new slash command is added, test that `/help` shows it, that the REPL starts, that fuzzy search finds it)
3. Do NOT run completely unrelated flows (e.g., if the diff only adds a tool, do NOT test `/session`, `/model`, or config commands)
4. If no existing flow covers the change, write a NEW ad-hoc test that directly verifies the changed behavior
5. Do NOT run unit tests, lint, typecheck, or any automated test suite. This is manual/functional QA -- interact with the CLI as a real user would.

## Step 6: Evidence Capture

After each significant test step, capture evidence using **text snapshots as primary evidence**.

For CLI testing (tuistory):

- Use `droid-control` skill for all tuistory interactions. Launch with:
  ```
  droid exec --skill "droid-control" "launch the wisp CLI in a tuistory session named qa-test with 110 cols and 36 rows, then send the command 'wisp --version'"
  ```
- Take text snapshots with `tuistory -s qa-test snapshot --trim`
- Embed snapshots directly in the report as fenced code blocks with descriptive labels
- Wait for UI to change before capturing (e.g., after a prompt, wait for the agent response)

**Important**: For the simple text REPL (not the Ink/Textual TUI), you can also use basic shell commands:
  ```bash
  echo -e "wisp repl\n/help\n/exit\n" | python -m wisp repl
  ```
  This is faster than tuistory for non-interactive scenarios.

## Step 7: Test Quality Gate

TEST QUALITY REQUIREMENTS:

1. CHANGE-SPECIFIC FIRST. Prioritize tests that directly verify the behavioral change in the diff. At least half your tests should test the new/changed feature itself.
2. INTEGRATION TESTS ARE VALID. Tests that verify the change integrates correctly with existing features are good (e.g., new slash command shows in `/help`, CLI starts without errors). These verify the change didn't break integration points.
3. NO UNRELATED FLOWS. Do NOT test features completely unrelated to the diff (e.g., don't test `/session` when only a tool changed, don't test config when only the REPL changed).
4. NO AUTOMATED TEST SUITES. Do NOT run vitest, npm test, or pytest as QA. This is manual/functional QA only.
5. NEGATIVE TESTS. Include at least 1 test verifying error handling or boundary conditions related to the change.
6. INTERACTIVE TESTING. Test by actually interacting with the CLI as a real user would.
7. INCONCLUSIVE IF UNSURE. If you cannot articulate what the PR changes, mark as INCONCLUSIVE rather than PASS.

## Step 8: Handle Failures

Never silently skip a flow. If a flow cannot complete, report it as BLOCKED with what was tried and how the user can fix it. Then continue to the next flow.

## Step 9: Generate Report

Generate the report at `./qa-results/report.md` using `.factory/skills/qa/REPORT-TEMPLATE.md`.

Report rules:
- Start with `## QA Report` heading followed by the test results table
- Result column MUST use emojis: :white_check_mark: PASS, :x: FAIL, :no_entry: BLOCKED, :warning: FLAKY, :grey_question: INCONCLUSIVE
- Keep it CONCISE. Table + short "Action Required" section + collapsed screenshots = entire report.
- Do NOT include setup/prerequisite steps as test rows. Only report rows that verify actual user-facing behavior.
- Put ALL evidence in a single collapsed `<details>` block
- For CLI evidence: embed text snapshots as labeled fenced code blocks

## Step 10: Suggest Skill Updates (Failure Learning)

After generating the report, check if any BLOCKED or FAIL results revealed a testing environment insight that would help future QA runs.

Format as a table with severity, collapsible fix prompts, and count in the heading.

Read the `failure_learning` field from config.yaml (`suggest_in_report`). Include the table in the PR comment report only. Do NOT write `skill-updates.json` (that's for `auto_commit` or `open_pr` modes).

Good suggestions (environment/workflow knowledge):
- "Ollama must be started with `ollama serve` before running LLM tests"
- "The React TUI requires `npm run build` in wisp-tui/ before `wisp tui --ink`"
- "MockProvider must be specified with `--model mock-model` for deterministic responses"
- "The REPL prompt may take 5-10s to appear on first run (model loading)"

Bad suggestions (skill bugs, not environment insights -- do NOT suggest):
- "Selector doesn't work" -- fix directly
- "Button text changed" -- expected from the PR diff
