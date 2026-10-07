---
name: mutation-probe
description: Prove new tests actually guard the code by breaking one load-bearing line at a time and checking that a test fails. Use after writing tests for a gate, rule, parser or process-control code, before calling a phase done. Covers the script, how to triage survivors, and the traps (concurrent runs, mid-probe edits, rtk and sed).
agent_created: true
---

# Mutation probe

A green suite proves nothing about tests that never fail. Break one line, run the tests, restore, and require a failure. Every phase of the reasoning core and the background jobs ended with one; each found real missing tests (a disclaimer in another sentence, a path-traversal id that only breaks with an *existing* id, a SIGSTOPped supervisor, kill-before-start).

## The probe

One table of `(file, old, new)`; for each: assert `old` is present, write the mutant, run the **narrowest suite that should catch it** with `-x --tb=no`, restore the original bytes in `finally`, print KILLED or SURVIVED. Print MISSING when `old` is not found (the code moved: update the table, do not skip). Keep the script outside the repo (scratchpad).

```python
orig = p.read_bytes(); src = orig.decode()
try:
    p.write_text(src.replace(old, new, 1))
    killed = subprocess.run([PY, "-m", "pytest", *tests, "-q", "-x", "--tb=no"], env={**os.environ, "HOME": tempfile.mkdtemp()}).returncode != 0
finally:
    p.write_bytes(orig)
```

Choose mutants that flip a decision (`if cond:` to `if False:`/`if True:`), drop a guard, widen a regex, swap a constant, or delete a call. Include the seams in the engine, not only the new module.

## Triage every survivor

1. **Real gap**: write the test that kills it (a positive and a negative case), rerun. Most survivors are this.
2. **Equivalent mutant** (behaviour identical, e.g. `==` vs `>=` where the larger case is handled first): record it in the design doc, do not chase it.
3. **Needs an environment the probe does not have** (Docker-only, Linux-only): name it, run that suite by hand once, say so in the report.
4. **A defect in the code** the mutation exposed: fix the code (the process-tree kill missed orphans only because a mutant forced the question).

## Traps

- **Never run it while anything else uses the same checkout**: the probe edits source files; a full suite or a live run in that worktree reads a mutant. Use `run_in_background` and leave the worktree alone until it finishes.
- **Do not edit tests while it runs**: later mutants are judged by a different suite. Collect survivors, then add tests.
- **A narrow test list hides wiring bugs**: when a survivor is in an engine seam, run the real-turn tests, not only the unit tests.
- Use `Edit` or a Python replace for source edits, not `sed -i` (the rtk hook mangled the path once). A syntax slip in a mutant looks like KILLED: keep the table small and read the first few results.
- Unreliable tests make survivors look killed: if a test is flaky, fix it first.

See also `verified-change-workflow` (the loop this belongs to).
