# PHASE_WORKSPACE_DOTENV.md — the workspace `.env` is written and never read

**Mission:** `PHASE_F57_DOTENV.md` §2's second finding. `provider_select._persist_env` writes
`<workspace>/.env`, and nothing reads it. This is the same `unwired-control` class as F57, but the
decision is different, because a repository can carry this file.
**Baseline:** branch `workspace-dotenv`, on `f57-dotenv` (F57 has not reached `main` yet: PR #30 and
the two follow-ups are still open). The tree is the user's WIP from `CONTEXT.md` §8, untouched.

---

## §1 — In one page

*Written when deliverable 2 lands (§3).*

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
