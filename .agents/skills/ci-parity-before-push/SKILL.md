---
name: ci-parity-before-push
description: Before pushing or opening a PR, run EVERY step CI runs (not just the tests) and, for any test that fails only in CI, find what differs. Use before git push, when a PR shows a red check you did not see locally, and when main goes red after a merge. Born from three misses in one day (ruff on #100, mypy and ruff on the reasoning branch, an order-dependent test that turned main red).
agent_created: true
---

# CI parity before push

CI is a list of steps; the test step is only the first. Steps run **in order and the job stops at the first failure**, so a green test step proves nothing about the lint and type steps after it, and a red later step hides behind a long test run (the 16-minute `test-python` job failed on the Ruff step after 10,470 tests passed).

## Do

1. **Read `.github/workflows/ci.yml` and run every step locally**, in order, on the exact branch you are about to push. For wisp: `pytest -m "not live"`, `ruff check wisp/`, `ruff check wisp_net/`, `ruff check tests/` (all three paths, not just the files you touched), bare `mypy` (the strict gate in `pyproject.toml`), `python -m compileall -q wisp/`. A targeted ruff on new files missed an unused import in an old test file that the branch inherited.
2. **After merging a base branch into yours**, rerun ruff and mypy: the merge can add errors you did not write.
3. **A PR stacked on another PR** (base is not `main`) may get no CI at all until retargeted: say so in the PR, do not read "no checks" as green.
4. Get CI status from the app's PR tools or a single `gh pr view --json statusCheckRollup`; do not poll in a loop. Identify the failing *step* (`gh run view <id> --json jobs`), then the failing test (`gh run view <id> --log-failed`), before changing anything.
5. **Check `main` after each merge** (`gh run list --branch main`): a merge can turn `main` red even though the PR was green, because PR CI runs on a merge ref that may differ from what lands.

## A test that fails only in the full CI run

It passes alone, so the cause is shared process state. Look for: a monkeypatch on a **module-global** (`asyncio.sleep`, `time.sleep`, `random`), leaked threads or loops from earlier tests, env vars, and `PYTHONHASHSEED`/order. Reproduce the *class* deterministically (a competing thread with its own loop sleeping 0.02 s reproduced the polluted `waits` list), then scope the patch to the module under test (replace the name inside that module with a shim that delegates everything but the one function) instead of patching the shared module.

## Prove the regression test, safely

A test that guards a fix should fail on the old code. Restore the old fixture temporarily, run the test **with a hard timeout**, then restore the file **byte-exact** and diff it (`cp` is aliased interactive here and silently refuses to overwrite: write bytes with Python and compare). Patching a sleep to a no-op can make a competing loop spin forever and flood a list: that run hangs; kill it and restore the file first.

## Tooling traps seen

`rtk` can print the wrong SHA (use `rtk proxy git rev-parse`); `ps | grep` from the tool did not show background jobs I had started, so "no process" proved nothing (use `pgrep -f` and check that the log or results file is still growing); a `cp -i` or `sed -i` through the hook can fail without failing the command.

See `verified-change-workflow`, `mutation-probe`.
