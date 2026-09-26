# PHASE_F57_DOTENV.md — `~/.config/wisp/.env` is read

**Mission:** F57 — the file `provider_select.store_key()` writes was never read. **No ADR**: the
patterns are decided — read once at the composition point (ADR-0002, ADR-0006), and *absence is a
configuration, invalidity is a refusal* (ADR-0058), with the brief's tolerance for a best-effort writer.
**Baseline:** branch `f57-dotenv`, on `register-source-pins` (merged with PR #30's CI fix). Tree = the
user's WIP in `CONTEXT.md` §8 — untouched.

---

## §1 — In one page

**F57 is FIXED.** `wisp/user_env.py::load_user_env` loads `~/.config/wisp/.env` as the **first
statement of `wisp.__main__:main`**, the console entry point that dispatches every CLI command —
before any `WispConfig` or provider reads the environment. **Two production files**: the reader
(new) and that call site. RED-first (7 failed before the reader), **11 passed** after; **2/2**
mutation probes caught; the differential over 21 CLI/provider/config suites is **374 passed before and
after, identical**.

**Read this before you next run `wisp`:** a real `~/.config/wisp/.env` exists on this machine and
supplies **`WISP_MODEL`, `WISP_PROVIDER`, `WISP_API_KEY`, `WISP_API_BASE`, `NVIDIA_API_KEY`,
`WISP_OLLAMA_URL`** — none exported in the shell measured. Until now all six were ignored; from this
landing they take effect in every `wisp` run where they are not exported. That *is* the fix — but it
may change which provider and model a bare `wisp` uses. Export a variable to override the file.

---

## §2 — Deliverable 1: the file is read, at one site, additively

### Where the load goes, and why there

The writer's variables are read in two ways, both measured: **directly from `os.environ`** —
`provider_select.resolve_key` (every provider-key lookup) and the OpenRouter/OpenAI providers at
construction — and **through `get_setting`** when a `WispConfig` is built. `WispConfig` is constructed
at about a dozen sites (headless, the SDK, the server, the TUI, ACP, the multi-agent and graph CLIs…),
so "before any consumer" cannot be one of them. **What they share is the console entry point.** Its
first statement is the one site that precedes them all. **Measured, not assumed:** importing
`wisp.__main__` reads **none** of the writer's eight variables (every environment read was
instrumented during the import), so loading at `main()`'s first statement is early enough.

A library importing `wisp` (the SDK) does **not** load the file — it never did, and a library should
not rewrite its host's environment. That is what keeps the fix additive.

### The semantics

| case | behaviour | test |
|---|---|---|
| file absent | no-op, silent — **the environment after `main()` equals the environment before it** | `test_no_file_changes_nothing` |
| key in the file, not exported | set — and `resolve_key` sees it | `test_a_key_in_the_file_reaches_the_consumer` |
| the production writer's own output | read back (`_upsert_env_file` → `load_user_env`) | `test_the_writers_own_output_is_read_back` |
| key already exported — even as `""` | **untouched**: the environment wins | `TestTheEnvironmentWins` (2) |
| unreadable: `0o000`, a directory, not UTF-8 | one warning (`could not read …`), nothing loaded, **no exception** | 3 tests |
| malformed line (`no equals`, `=value`, `export K=V`) | skipped, warning **names the line number**; the rest loads | `test_a_malformed_line_…` |
| any case | **no value reaches a log line** — warnings name a line, never content | `test_no_value_reaches_stderr` |
| the load site | **exactly one** call in `wisp/` (AST), and it is `main()`'s **first** statement | `test_the_loader_is_called_once_first_in_main` |

The format is the writer's and no more: `KEY=VALUE`, `#` comments and blank lines. No quotes, no
`export`, no interpolation.

### Verification

| check | result |
|---|---|
| RED, before the reader | **7 failed, 4 passed** — the 4 are invariants that held already (the environment wins, absence changes nothing, no secret logged) |
| after | **11 passed** — the guard's first cut failed once more, correctly: the loader's *import* was `main()`'s first statement, not its call; the import moved to module level |
| **probes** | remove the call site → **CAUGHT** (7 tests); move the call below `main()`'s setup → **CAUGHT** (the first-statement guard). `__main__.py` restored byte-identical, no bytecode written (the lesson of `PHASE_REGISTER_SOURCE_PINS.md` §4) |
| **differential** | 21 files — `test_provider_select*`, `test_config*`, `test_cli_surface_e2e`, `test_entry*`, `test_main*`, `test_headless*`, `test_provider_*` — on a clean worktree of the pre-change commit `9691614` and on this change: **374 passed / 374 passed**, empty failure sets both sides. *(The first attempt ran nothing — a zsh glob aborted the file list and both sides reported 0 failing; caught because nothing was collected, and re-run with a floor.)* |
| `ruff` | new files clean; `wisp/__main__.py` 0 → 0 |

### Findings — recorded, not fixed (the mission is bounded)

1. **The file now outranks `config.json`.** The loader writes into the *environment* layer, and
   `get_setting` resolves environment > `config.json` > defaults. `persist()` writes both files
   together, so they normally agree; where they disagree — `config.json` edited by hand or by
   `/config` — **`.env` now wins**. The brief states only environment-vs-file precedence; this is the
   question it did not ask.
2. **The workspace `.env` is still write-only.** `_persist_env` also writes non-secret keys
   (`WISP_PROVIDER`, `WISP_MODEL`, …) to `<workspace>/.env`, and nothing reads that either. Same class,
   a different file, and a different decision (a repo-local file an attacker can commit is not the
   operator's own file).
3. **F57 has no open-items row.** The brief expected `CURRENT_OPEN_ITEMS.md` to carry one; that
   register is built from `CONTEXT.md` §12, the ledger's per-item tables and ADR residuals, and F57 — a
   ledger §0 finding — was never among them. **No row was invented to be closed.**
4. **A hand-written `KEY="value"` keeps its quotes** — the writer never writes them, and the brief
   forbids extending the format. An operator who quotes a value gets the quotes.

### The records

`WISP_MIGRATION_STATUS.md:178` — the F57 row's **status cell** changes to `**FIXED 2026-09-26** (was
*OPEN, recorded*)`, not an appended note beside an unchanged headline (F8's self-contradicting row is
the precedent to avoid). `CURRENT_FINDINGS.md` — `F57` regenerated `FIXED`, re-pinned to that cell,
tripwire `tests/test_dotenv_is_read.py`.
