---
name: verified-change-workflow
description: Make a change to a safety-relevant or shared-policy part of a codebase and prove it — measure, decide, apply; RED first; test through the production path; mutation-probe and positive-control every check; compare against a known-good baseline; open a PR for the human to merge; record what was learned. Use for security fixes, permission or policy changes, gate/filter work, model evaluations, and repairing a red branch.
agent_created: true
---

# Verified change workflow

The method used to land PRs #45–#59 and ADR-0074 on wisp. It exists because the failures we hit were all one
shape: **a claim tested at one layer while the real path had several.** Use it when a wrong answer would let
something unsafe run, or when a red suite has to be explained rather than silenced.

## The loop: measure, decide, apply

1. **Measure.** Reproduce the problem at runtime before touching code. Print the event, the status, the message.
   Name which layer produced the refusal or the failure. If you cannot reproduce it, say so and stop.
2. **Decide.** State the smallest change, what it costs, and what it does not cover. If it widens a permission
   mode, changes a pinned policy or a published taxonomy, or spends money, it is the human's decision: give the
   trade-off and a recommendation, and ask. Otherwise decide and proceed.
3. **Apply.** RED first, then the change, then the probes below. One logical commit per idea.

## Test rules

- **RED first.** Write the test, run it, confirm it fails *for the reason you expect* (a collection error is a
  weak RED; a wrong-reason failure is a wrong test). Then implement.
- **Production path, not one gate.** Drive the object every transport uses (for wisp: `ToolExecutor.execute`),
  replace only the tool body, and assert what a model would see. If a "fixed" case still fails, print the event:
  the next layer's message is the next gate to fix. List every place that enforces the rule by name before you
  declare it done.
- **Positive control for every negative result.** "The model scored 0/6" means nothing until a scripted model
  that does the thing passes the same pipeline. "This is refused" means nothing until the allowed case runs.
- **Mutation probe every load-bearing line.** Break it with one edit, run the tests, restore, and assert the file
  is byte-identical to the original:

  ```python
  orig = p.read_bytes(); assert orig.count(old) == 1
  p.write_bytes(orig.replace(old, new))
  try: run_tests()
  finally: p.write_bytes(orig)
  assert p.read_bytes() == orig
  ```

  A mutant that survives is an equivalent mutant or a missing test. Add the case that separates them.
- **Do not compare two things that share code.** If both surfaces call one function, state the expected answers
  independently, or the test is circular.
- **Check the boundary values of shared lists.** Duplicated allow/deny lists drift; add one agreement test.

## When something is red

- Run the **exact failing ids on the known-good baseline** (`origin/main`) first. Passing there means the cause
  is local; failing there means it is not yours.
- Bisect with a predicate pinned to the **exact failure** (and a hard timeout); treat "cannot run" as skip.
- A **hang** is a failure mode: hard timeouts everywhere; locate the stuck test by counting progress characters
  against `pytest --collect-only` order.
- A CI flake: confirm it passes locally and on `main`, re-run only the failed job, and fix it if it recurs.
- Never edit a test to match new behaviour without saying which decision the behaviour came from. Two groups look
  alike: **stale setup** (authorise the executor explicitly) and **a contradicted invariant** (a pin, a parity
  test). The second is a policy question; surface it.

## Evaluating a model

Hermetic: temporary HOME and workspace, scrubbed environment (credentials only by an explicit list of names),
only read tools declared read, no fault-injection tools. Score on evidence (a tool call), on named facts, and on
never trying to change anything; read refused attempts from the error text because they are not in `tool_calls`.
Record refused loads and hand-offs without failing. Change the scorer, then **re-score saved output**, not the
model. One sample is a demonstration, not a rate. Never print a key.

## Working with the human

- **Open a PR; the human merges.** Stop at green and say so. Never push their unpushed commits, delete remote
  branches, or touch their uncommitted files. Work in a scratch worktree on a branch; fast-forward only when asked.
- Report faithfully: what was verified, what was not, and any number you corrected.
- Write PR bodies and commit messages from a **quoted heredoc** (`<<'EOF'`); an unquoted one executes backticks.

## Shell hygiene

`rtk proxy git ...` for truth (the wrapper rewrites `git log` and `grep`); zsh does not word-split `$VAR`;
macOS has no `timeout`, use Python's `subprocess` timeout; the `pyt` wrapper drops `PYTHONPATH`; one suite per
worktree; do not delete a file a background job writes; do not signal a test process.

## After a milestone or a phase

Append the lesson to `AGENTS_LEARNING.md` (one `##` section, search words in it, evidence named), update the
session `CONTEXT.md` (state, what is done, what is next), and note any decision in the ADR log. Keep the
knowledge base as plain markdown searched with BM25 (see `CLAUDE.md`).
