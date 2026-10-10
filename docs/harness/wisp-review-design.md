# Wisp's own Qodo: review, rules and triage (design, 2026-10-11)

The owner's pasted capability list (item 7, "Git and GitHub intelligence") asks for PR triage and review, and said Wisp never got "its own Qodo". This page is the research, the audit of what Wisp had, the design, and what is deliberately not built yet.

## 1. What Qodo is (research; self-reported product claims, weighed accordingly)

Sources: Qodo's documentation (`docs.qodo.ai`: platform overview, context engine), the open-source PR-Agent README, and secondary write-ups. Qodo describes a code-review platform with these parts:

| Qodo part | What it does | Wisp analogue |
|---|---|---|
| **Merge / PR-Agent** (open source core) | Reviews a pull request in about one model call per tool (`/describe`, `/review`, `/improve`, `/ask`), with PR compression for large diffs and structured output | `wisp review` |
| **Rules** | Org and repo rules applied across the toolchain; "mined" from PR history; reported as violations | `review-rules.toml` plus deterministic checks |
| **Context engine** | Retrieves repository context beyond the diff, across repos | the review lens runs as a read-only agent with grep, symbol search and the repo map; no cross-repo index (not built) |
| **Ticket compliance** | Checks the change against the linked ticket's requirements | not built (needs an issue reader; noted below) |
| **Cover / diff-test agent** | Generates tests for changed code | not built (the objective loop in #114 is the natural engine; noted below) |
| **Command / CLI agents** | Runs the review agents from a terminal and CI | `wisp review`, `wisp triage`, `/review`, `/triage` |

Two things Qodo's marketing leaves open are where Wisp can be stricter: a review's verdict comes from a model, and a finding is whatever the model says.

## 2. What Wisp had (read in the source on 2026-10-11)

`wisp/server/routes/review.py` only: `POST /api/review/pr`, `/diff` and `/best-of-n`. One prompt, the diff **cut at 15,000 characters without saying so**, a headless read-only agent, the model's own `approval` as the verdict, a model-written `line` and no check that the quoted code exists. No rules, no secret or test-tampering checks, no CLI, no REPL command, no triage and no resolve step. The git and `gh` tools (`gh_pr_view`, `gh_pr_list`, `gh_pr_checks`, `gh_pr_comment`, `gh_pr_merge`...) exist for the agent, and `wisp/core/gates/` has a secret scanner and a manifest classifier.

## 3. Design: a finding is a claim the harness checks

The Wisp rules that decide the design: evidence beats confidence; text from a model or a diff is data, never authority; unknown is not clean; everything that loops or reads has a bound.

1. **Deterministic checks first, no model.** Secrets in added lines (reusing `core/gates/secrets`, and the report never echoes the secret), conflict markers, syntax errors in the post-image of changed Python, JSON and TOML files, weakened tests (added `skip`/`xfail`/tautologies, net removed assertions, a deleted test file), added public symbols that no test mentions, dependency and CI files changed (reusing `core/gates/deps.is_manifest_path`), debug leftovers, an oversized change, and the repo's own rules.
2. **Model lenses are optional and grounded.** Three lenses (correctness, security, tests) get the diff as delimited data plus the repo rules. Each finding must quote the code it stands on. The harness looks the quote up in the post-image of that file: found, the finding keeps the **harness's** line number; not found, it is dropped and counted ("N model claims could not be tied to the diff"). A model finding is capped at `warn`: **only established evidence can block**.
3. **The verdict is the harness's.** `blocked` (an established blocker), `incomplete` (something was not reviewed, a lens failed, or the diff did not parse completely: unknown is not clean), `attention` (warnings), `clean` (only notes, with full coverage). `clean` means "nothing found by these checks", never "correct". The model's own opinion is shown as advice and never changes the verdict.
4. **Coverage is part of the answer.** Large diffs are split per file and per hunk; anything beyond the budget, binary, generated or vendored is listed under **Not reviewed** with the reason. Nothing is cut silently (the old route cut at 15,000 characters).
5. **Rules** are a TOML file (`.wisp/review-rules.toml`): `id`, `text`, `severity`, optional `paths` globs and an optional `forbid` regex over added lines. A rule with `forbid` is checked deterministically; its text also goes to the model lenses. Unknown keys and bad regexes are errors (fail closed), not ignored.
6. **Triage is read-only.** `wisp triage` reads `gh pr list` (one JSON call) and classifies each open PR with the outcomes of the capability list: ready for review, changes requested, CI failing, conflicting, stale, possible duplicate (changed-file overlap and title overlap), missing tests, missing requirements (no body and no linked issue), security review (auth, secrets, CI or dependency paths), stacked on another PR, or human decision. It never merges, closes, comments, labels or approves; that is a separate authorization.
7. **No posting.** Posting review comments to GitHub is not built; the `gh_pr_comment` tool exists, and posting on the owner's behalf needs an explicit yes each time.

Surfaces: `wisp review [--staged | --base B --head H | --commit SHA | --pr N] [--rules FILE] [--no-model] [--lens NAME] [--run-tests] [--json] [--fail-on blocked|incomplete|attention]`, `wisp triage [--limit N] [--repo OWNER/NAME] [--stale-days N] [--json]`, and `/review`, `/triage` in the REPL (native commands, so `/help` lists them). Global flags go after the subcommand, as for every `wisp` subcommand (`wisp review -w DIR`). `--run-tests` runs the affected tests of the **local** checkout through the harness; a failing test is established evidence and blocks. It is refused for `--pr` (it would execute a stranger's code). The model lenses run as a headless **read-only** agent, one fresh session per call, so a lens can read the repository around the diff but cannot change it and one hostile diff cannot leave instructions in another lens's context.

## 4. Layout

`wisp/review/`: `diff.py` (unified-diff parser with real new-file line numbers), `types.py`, `checks.py`, `rules.py`, `grounding.py`, `lens.py` (prompts, parsing, chunking), `engine.py` (orchestration with an injected model runner), `report.py` (markdown and JSON), `source.py` (git and `gh` reads with ref validation), `triage.py`, `cli.py`. The pure parts have no I/O; `source.py` and the default model runner are the only edges.

## 5. Proof plan

Real git repositories for the diff source and the parser (property: every added or context line's `new_no` equals the line in the file at that number), the real deterministic checks on real diffs, the engine with a scripted model (grounded, ungrounded, a hostile diff that tells the reviewer to approve, a model that fails), `wisp review` as a real subprocess, a pty REPL for `/review`, `wisp triage` against a scripted `gh` with real `gh pr list` JSON shapes, then a mutation probe.

## 6. What building it found

Each of these was found by a test or a real run, not by reading, and each has a test.

- **The secret gate hides vendor tokens behind the words around them.** `core/gates/secrets.find` merges overlapping spans, so `token: <vendor token>` comes back as a generic `secret-assignment` or as `entropy`, kinds the gate itself does not treat as certain. The first version of the review therefore demoted a real GitHub or Stripe key to a warning. The certainty is now decided on the bare token (a parametrized test over four surrounding shapes and three vendors). The gate itself is unchanged.
- **Wisp's own state looked like the user's change.** The first run in the real REPL listed `.wisp/wisp.db`, `.wisp/wisp.db-wal` and `.agent/runtime.log` as new files in the user's uncommitted work. `.wisp` and `.agent` are now excluded from the uncommitted and staged diffs (the same lesson as the keep-or-revert snapshot).
- **`run_headless` shares state across calls.** It caches one composition root and defaults to one session id (`headless`). Reused as it is, every lens would see the previous lens's conversation, and a root started on one event loop would be handed to a second review on another. Each call now gets its own session id, and every review in a process runs on one daemon-thread event loop; a real `SIGINT` test shows Ctrl-C cancels the model call.
- **Silent truncation was the old behaviour.** `/api/review/pr` cut the diff at 15,000 characters and said nothing. Chunking here puts every file in a chunk or in the "not reviewed" list with a reason (property-tested), and an unreviewed file makes the verdict `incomplete`.
- **A model finding's own claims are data.** Its quote is looked up in the diff, its line number is replaced by the harness's, its text is stripped of escapes and secrets, its severity is capped at warn, and its verdict is never read. A hostile model reply and a hostile diff are both tests.

## 7. Proof

414 tests under `tests/review/`: the parser against real `git diff` output (every post-image line number is the line in the real file), the checks on real diffs, rules, grounding (including hostile model replies and a hostile diff), the engine with a scripted model (timeouts, failures, concurrency, caps), reading from real repositories (a hostile repository configuration cannot run a program through the diff, refs and paths cannot be option- or traversal-shaped), triage on the JSON `gh` returns and a `gh` that records what it was asked to run, the CLI in process and as a real process, and the REPL in a pty. A mutation probe of 149 mutants across the nine modules. 22 survived the first run and each produced a test or the deletion of dead code; all are killed. The 22: text before the first file header not counted; `pdb.set_trace()` alone not a leftover; a change with few files but very many lines not noted as large; ANSI escapes left in a model's message (only the escape character was asserted away); a quote stitched from the last line of one hunk and the first of the next; `a/`, `b/` and `./` prefixes on a model's file name; a prompt that no longer forbids a verdict (the test asserted the word, not the sentence); an unknown lens accepted by the engine; a hunk cut short ignored when the caller reports no residue; option-shaped refs and `..` refs (the test matched git's own usage text instead of the validator's message); a symlink inside the workspace followed; a path with a parent segment that lands inside accepted; a diff over the size bound read; docs about security counted as security paths; one shared file, and any small overlap, counted as a duplicate pull request; a fork branch called `main` making the default branch look stacked; `--no-model` still building a model runner; `wisp triage --limit` unchecked (the test passed only because `gh` was missing). Two of the 22 were unreachable code and were deleted: a `binary` check in grounding (a binary file has no hunks) and an `@{` check on refs (the ref pattern already rejects `{`).

## 8. Not built, stated

- **Resolve** (Qodo's PR resolver): turn a confirmed finding into an objective for the loop in #114 whose acceptance is "the finding is gone and the suite still passes". It depends on #114 and is the next slice.
- Test generation (Qodo Cover), ticket compliance, cross-repo context, rules mined from PR history, posting comments.
- `--run-tests` covers Python projects only (it uses the existing import-graph lookup and pytest); for anything else it records a gap rather than claiming the tests pass.
- Review of a pull request does not have the head's file contents unless the head is available locally, so the parse check cannot run and the verdict is `incomplete` rather than `clean`.
- The lens quote check finds code, not meaning: a model can quote a real line and still misread it. That is why a model finding is a warning and never a blocker.
- The server's `/api/review/*` routes still run their old single-prompt path; making them delegate to `wisp/review` is a separate change because the macOS app uses them (one authority per decision is the goal; until then there are two).
- A concurrent writer, a rebased PR or a force-push between reading the PR list and the diff is not detected.
