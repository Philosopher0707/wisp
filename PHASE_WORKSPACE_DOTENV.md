# PHASE_WORKSPACE_DOTENV.md — the workspace `.env` is written and never read

**Mission:** `PHASE_F57_DOTENV.md` §2's second finding. `provider_select._persist_env` writes
`<workspace>/.env`, and nothing reads it. This is the same `unwired-control` class as F57, but the
decision is different, because a repository can carry this file.
**Baseline:** branch `workspace-dotenv`, on `f57-dotenv` (F57 has not reached `main` yet: PR #30 and
the two follow-ups are still open). The tree is the user's WIP from `CONTEXT.md` §8, untouched.

---

## §1 — In one page

**For the operator: a bare `wisp` uses the same provider and model it used before.** Nothing ever
read `<workspace>/.env`, so removing its writer changes no process's configuration. That is measured:
across 6 `persist()` / `store_key()` scenarios, the resolved `WispConfig`, the environment, the
operator's `~/.config/wisp/.env` and `config.json` are **byte-identical** before and after.

**What does change:** `/provider`, `/model`, the setup wizard and the server's model route no longer
write `WISP_PROVIDER` / `WISP_MODEL` / `WISP_API_BASE` / `WISP_OLLAMA_URL` into the project you are
working in. Until now they appended those lines to the project's own `.env`, and changed the file's
mode to `0600`. **Existing workspace `.env` files are left as they are.** One written before
2026-09-14 may hold **API keys**; this repository's does. Check any project where you ran `/provider`
before then.

**The decision** (§2, deliverable 1, `7a8fe8b`): **not read, writer removed.** Measured:

- If the file were read, even after the operator's own file, a cloned repository's `WISP_API_BASE`
  sent the operator's API key to the repository's endpoint.
- Its `WISP_OLLAMA_URL` sent the prompt there.
- Both keys are "non-secret". The line that matters is not secret versus non-secret but **whether a
  key names where data goes**.

No ADR: nothing a repository carries reaches the process, before or after.

**The change** (§3, deliverable 2): `_persist_env` writes `~/.config/wisp/.env` only. It takes the
path from `user_env.user_env_path()`, so the writer and F57's reader name one file. The change also
touches three user-facing strings that pointed at `./.env` and one test that pinned the old write.

- Guard: `tests/test_workspace_dotenv_not_written.py` (7). **RED-first: 4 failed, 3 passed.**
- **2/2** mutation probes caught.
- Test differential over 24 files: **425 passed** before and after, the same 3 pre-existing
  failures both sides.

---

## §2 — Deliverable 1: the measurement, and the decision it picks

Every number below comes from `scripts/workspace_dotenv_measurement.py`, run in this environment.

- It drives the production code in subprocesses, each with a private `HOME`, so the operator's real
  `~/.config/wisp/.env` is neither read nor touched.
- Every value is a synthetic sentinel.
- Two local listeners stand in for endpoints: one named by a cloned repository, one by the operator.
- The script only calls a listener if the resolved endpoint is one of its own. The first cut did
  not have this restriction. Its "today" case fell back to the default provider and sent the
  sentinel prompt to this machine's real Ollama, which returned 400. The script now contacts only
  its own listeners.

### 1. What `_persist_env` writes (read from the code, then driven)

`persist(update)` calls `_persist_env(update)`, which maps config keys to variables through
`env_map`. It then writes two files:

- `~/.config/wisp/.env` gets every mapped key.
- `<workspace>/.env` gets only the keys whose name does not contain `KEY`. That filter is `b657e21`,
  2026-09-14, *"Keep secrets out of the committable workspace .env"*.

Driven with every mapped key present:

| variable | value class | `<workspace>/.env` | `~/.config/wisp/.env` |
|---|---|---|---|
| `WISP_PROVIDER` | non-secret: selects the backend | written | written |
| `WISP_MODEL` | non-secret: selects the model | written | written |
| `WISP_API_BASE` | non-secret: **where requests, and the key, are sent** | written | written |
| `WISP_OLLAMA_URL` | non-secret: **where requests, and the prompt, are sent** | written | written |
| `WISP_API_KEY` | secret | — | written |
| `OPENAI_API_KEY`, `NVIDIA_API_KEY`, `OPENROUTER_API_KEY` | secret | — | written |

The workspace directory is resolved in this order:

1. `update["workspace"]`
2. `WISP_WORKSPACE`
3. `config.json`'s `workspace`
4. the cwd

`persist()` also stores `workspace` in `config.json`. So a later `persist()` from any directory
writes to the **last remembered** workspace. Both paths were driven (§5 below).

### 2. What reads each key

These four variables have consumers, but they read them **from the process environment**, never
from a file. They go through `get_setting` / `WispConfig` (`config.py`'s `SETTINGS_SCHEMA`) and
`providers/openai.py:85` (`WISP_API_BASE`).

**The file itself has no consumer.** I searched `wisp/` for every way a `.env` gets read:

- `dotenv`, `load_dotenv`, `dotenv_values`, `find_dotenv`: no match.
- `env_file`, `BaseSettings`: no match.
- `pyproject.toml`: no `python-dotenv`, no `pydantic-settings`.

The only `.env` reader in `wisp/` is F57's `load_user_env`. It is called once, with no path, so it
reads `~/.config/wisp/.env` only. **`<workspace>/.env` is written and nothing reads it.**

Three user-facing strings direct the operator to it:

- `providers/openai.py:212` claims a key *"will be … saved to … ./.env"*. This has been false since
  `b657e21`: a key never goes there.
- `repl/commands/provider.py:374` and `cli/commands/model.py:304` both say *"set WISP_API_KEY in
  .env"*. A key put in a workspace `.env` has no effect.

### 3. What would happen if the file were read (driven)

**Setup.** A cloned repository carries a `.env` containing only keys the production writer itself
writes there: `WISP_PROVIDER=openai`, `WISP_API_BASE=<repository's endpoint>`,
`WISP_MODEL=attacker-model`. The operator's own `~/.config/wisp/.env` holds their key. A `wisp`
process starts in the repository through the real entry point (`main()`, then F57's load), builds
its `WispConfig` and provider, and makes the provider's first request.

**The counterfactual** loads the workspace file *after* the operator's file, using the same loader.
`load_user_env` never overwrites a set key, so **the operator's file wins**. This is the precedence
brief shape 2 asks for.

| case | resolved provider / model / endpoint | request reached the repository's endpoint | carrying |
|---|---|---|---|
| **today** (file not read) | `ollama` / `""` / the default | **none** | — |
| read; operator's file holds only a key | `openai` / `attacker-model` / **the repository's** | `GET /v1/models` | **the operator's API key** (`Authorization: Bearer …`) |
| read; operator's file also pins provider and base | `openai` / `attacker-model` / the operator's | none | — |
| read; `WISP_OLLAMA_URL` only, no key anywhere | `ollama` / `""` / **the repository's** | `POST /api/chat` | **the operator's prompt** |

**The answer to the brief's question: yes.** If the file were read, a repository could change the
process's behaviour before the operator sees the file. Two of the four keys the writer puts there
are exfiltration vectors:

- `WISP_API_BASE` sends the operator's key to the repository's host.
- `WISP_OLLAMA_URL` sends the conversation there, including every file the agent reads.

This needs no secret in the repository's file. The "operator wins" precedence only protects an
operator who has already pinned the endpoint in their own file. Pinning it is not the default:
`persist()` writes `api_base` only when the caller passes one.

### 4. The decision

**Measured against the brief's hypotheses:**

- **Shape 2** (read it, operator wins) is **falsified**: row 2 of the table.
- **Shape 3** (read only opted-in or non-secret keys), and the fourth shape the brief suggests (read
  non-secret keys, reject secret ones loudly), are **falsified on their axis**. The writer already
  writes only non-secret keys, and the exfiltration used only those. *Secret versus non-secret is
  the wrong line.* The line the measurement draws is **whether a key names where data goes**
  (`WISP_API_BASE`, `WISP_OLLAMA_URL`).
- An allow-list that excludes those two would leave `WISP_PROVIDER` and `WISP_MODEL`. Reading them
  would be a new feature (per-project model selection), not a repair: nothing ever read them from
  this file, and `config.json` already persists both globally. It would also be a behaviour change
  on a path a repository controls, so it would need its own ADR. It is not this mission.

**Decision: shape 1. The workspace `.env` is not read, and the writer that creates it is removed.**

| | |
|---|---|
| **keys read from `<workspace>/.env`** | none |
| **precedence** | not applicable; the file is not a configuration source |
| **guard** | the writer is gone: `persist()` creates or modifies no file outside `~/.config/wisp/`. F57's single load site stays the only `.env` reader and passes no path. Both are asserted on the production path (§3). |
| **reversal trigger** | an operator request for **per-project** provider or model selection. It would get its own ADR, an allow-list that **excludes every endpoint-naming key** (this table's rows 2 and 4 are the evidence), and a file the operator has visibly approved. It would not reuse `.env`, which repositories already carry for their own purposes (§5). |

**No ADR is appended.** The corpus requires one when a decision changes behaviour on a path a
repository can influence. This decision does not read the file, so nothing a repository carries
reaches the process: the "today" row stays today's behaviour. The only behaviour removed is a write
the operator's session makes into the repository, and no repository can influence that. The brief
names this case: *"If the decision is 'do not read it', no ADR is needed."*

### 5. What the removal also stops (driven): the writer's side effects on the operator's projects

| case | result |
|---|---|
| a project already has its own `.env` (`DATABASE_URL`, `DEBUG`; mode `0644`) and `/provider` runs there | wisp **appends** `WISP_PROVIDER`, `WISP_MODEL` to the project's file and **changes its mode to `0600`** |
| a later `persist()` from another directory, same `HOME` | writes into the **remembered** workspace's `.env` again, not the cwd's |
| no workspace anywhere (fresh `HOME`) | creates `.env` in the **cwd** |

The HTTP route `server/routes/models.py:134` also calls `persist()`, so a model switch over the API
writes into the server's workspace.

### Findings recorded, not fixed (the mission is bounded)

1. **Workspace `.env` files written before `b657e21` can hold secrets.** This repository's own
   `.env` holds `WISP_API_KEY` and `NVIDIA_API_KEY`; key names were listed, values were not read.
   It is git-ignored here, but another project's `.env` may not be. Removing the writer does not
   delete these files: they are the operator's, and deleting them is not wisp's call. **The
   operator should check any project where they ran `/provider` before 2026-09-14.**
2. **`store_key` makes `config.json`'s save fail.** It passes `key_<provider>` in the update, and
   `save_config` refuses unknown settings: *"Cannot save config with invalid values: Unknown
   setting: 'key_openai'"* (driven, §1). So `config.json` is not updated on that call, and only the
   `.env` files are. This is out of scope; it is the F57 file's concern, not the workspace file's.

---

## §3 — Deliverable 2: the writer, removed

### The change

| file | change |
|---|---|
| `wisp/provider_select.py` | `_persist_env` writes `user_env_path()` (`~/.config/wisp/.env`) and nothing else. Gone: the workspace block (the `KEY` filter, the four-step workspace resolution, the second `_upsert_env_file`). The docstrings of `persist`, `_persist_env` and `store_key` name the one file. |
| `wisp/providers/openai.py:212` | the 401 hint said a key *"will be … saved to ~/.config/wisp/config.json and ./.env"*. Both halves were wrong: a key never reached `./.env` after `b657e21`, and for the three providers with a key slot `store_key`'s `config.json` save fails (§2, finding 2). It now names `~/.config/wisp/.env`. |
| `wisp/repl/commands/provider.py:374`, `wisp/cli/commands/model.py:304` | *"set WISP_API_KEY in .env"* now reads *"… in ~/.config/wisp/.env"*, the file that is read. |
| `tests/test_provider_select.py` | `test_persist_env_writes_both_env_files` pinned the removed write. It is now `test_persist_env_writes_only_the_operators_env_file`: the operator's file gets prefs and keys, and the workspace gets nothing. |

That is the whole change. `_upsert_env_file` keeps its behaviour: one caller, and its `0600` mode is
right for the operator's file.

### The guard — `tests/test_workspace_dotenv_not_written.py`

| test | holds |
|---|---|
| `test_a_named_workspace_gets_no_env` | `update["workspace"]`: no `.env` there or in the cwd |
| `test_the_workspace_from_the_environment_gets_no_env` | `WISP_WORKSPACE`: no `.env` |
| `test_a_projects_own_env_is_left_byte_identical` | a project's `.env` at `0644` keeps its bytes and its mode |
| `test_store_key_writes_only_the_operators_file` | the key path, through `persist()` |
| `test_no_file_is_created_outside_the_operators_config` | **the whole temp tree**, not a list of expected paths, so a new write site anywhere is caught |
| `test_load_user_env_is_called_without_a_path` | the reader half: F57's load site (AST, F73) passes no path, so no workspace file becomes a configuration source |
| `test_the_default_path_is_the_operators_own` | `user_env_path()` resolves under `HOME` |

- **Observation point** (F96): the real `persist()` / `store_key()`, run in a subprocess with a
  private `HOME`.
- **Floor** (F81): every write test asserts that `~/.config/wisp/.env` *was* written with the keys
  passed, so a `persist()` that wrote nothing cannot pass "no workspace file" vacuously. The AST test
  asserts it found the load site.
- **Silent on a legitimate change** (F92): the tests observe the disk, not `_persist_env`'s spelling.

### Verification — the sets

| check | result |
|---|---|
| RED, before the change | **4 failed, 3 passed**. The 4 are the workspace writes. The 3 hold already: `store_key` only ever wrote `KEY` variables, which the old filter kept out of the workspace; the reader passes no path; and the default path. |
| GREEN | **7 passed**; with `test_provider_select.py` and F57's `test_dotenv_is_read.py`, **50 passed** |
| **behaviour differential** | `persist()` / `store_key()` in 6 scenarios (every key, a provider switch, two `store_key` sequences, an explicit workspace, a cleared key). Each records `persist()`'s return, the environment delta, the resolved `WispConfig`, the operator's `.env` and `config.json` bytes, and stderr. **Before** (a clean worktree of `7a8fe8b`) and **after**: **byte-identical**, 2863 bytes. |
| **test differential** | 24 files (F57's 21-file families, plus `test_dotenv_is_read`, `test_setup_wizard`, `test_v04_subsystems_integration`), one pytest at a time with `--basetemp`. Before: **425 passed, 3 failed**. After: **425 passed, 3 failed**. The failure sets are **identical**: `test_v04_subsystems_integration.py::TestScenarioBSandboxAndServerAuth::{test_benign_command_routes_strictly_through_provider, test_traversal_shaped_command_still_confined, test_unconfined_fallback_is_loud}`, the sandbox scenario, which does not touch `.env`. *(My first comparison was garbage: the `rtk` wrapper rewrote `grep`'s output. It was redone in Python, stripping ANSI codes.)* |
| **the measurement, re-run** | `writer.workspace_env`: 4 keys → **absent**. The project's own `.env`: `0600` plus two appended lines → **`0644`, untouched**. The cwd fallback: written → **absent**. The cloned-repository and keyless rows: **identical**. The file was never read, so the removal cannot move them. |
| **probes** | (1) re-add a workspace `_upsert_env_file` in `_persist_env` → **CAUGHT** (5 failed). (2) `main()` calls `load_user_env(Path.cwd() / ".env")` → **CAUGHT** (the AST test). Both files restored **byte-identical** (sha256), `__pycache__` purged, no bytecode written (`PHASE_REGISTER_SOURCE_PINS.md` §4). |
| **the corpus guards** | the four register suites, the entry point, ADR-0062's editorial guard, PR #30's review fixes, this guard, F57's guard and `test_provider_select.py`, in one pytest with `--basetemp`: **263 passed** |
| `ruff` | the 4 production files and 2 generators: **0 → 0** each (against the `7a8fe8b` worktree); the new guard and the measurement script: clean |
| **the canonical block** | **not run.** The host had about 136 MB free (8,726 pages), starved under F111. |

### The records

- **`WISP_MIGRATION_STATUS.md:178`**, the `F57` row. Its status cell is unchanged (`FIXED`, still
  true of the file it names). The row gains the workspace file's sentence: *writer REMOVED
  2026-09-26, the file deliberately not read*.
- **`CURRENT_FINDINGS.md`** has a new §Findings subsection, *"Findings whose scope a later landing
  extended"*: `F57`, the workspace file, the same `unwired-control` class, `FIXED` by removal,
  tripwire named. **No `F`-number was coined.** The register is total over `F1`–`F104`, and numbering
  a finding is a decision about the log (the page's own rule). This is F57's writer's other file,
  named by F57's own report.
- **`CURRENT_OPEN_ITEMS.md`**: no row, because the item never had one (like F57, `PHASE_F57_DOTENV.md`
  §2 finding 3). None was invented.
- **`CONTEXT.md`**: §0.0.21, the phase table, §3 and §13 (the §13 row landed with the report in
  `7a8fe8b`, R7). §3 also backfills `1c24a72`, `e97d22f`, `083ec71` and `d1e5921`.
- **`CURRENT_OPEN_ITEMS.md`'s 38 `CONTEXT.md` §12 pins** moved when §0.0.21 was inserted, and
  were re-pinned. The source check's own suggestions disagreed: shifts of 21, 23 and 24, because
  `locate` picks the nearest copy of repeated words. So the pins were mapped through the diff of
  `CONTEXT.md` (`difflib`, equal blocks only): 38 moved, 0 unmapped, every one a source pin.

### Not done

- **No workspace `.env` was deleted.** Not on this machine, and not by code. The files are the
  operator's.
- **`store_key`'s `config.json` failure** (§2, finding 2) is recorded, not fixed.
