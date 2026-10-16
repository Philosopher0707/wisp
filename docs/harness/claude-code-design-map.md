# Claude Code's design, from public sources, next to Wisp

Written 2026-10-13. A reference note for deciding what Wisp should borrow. It describes Claude Code **2.1.274** (installed by Homebrew) from three sources only:

1. **The Claude Agent SDK for Python, 0.1.68 (MIT).** Its public types (`claude_agent_sdk/types.py`) and its own transport code (`_internal/`). The bundled CLI under `_bundled/` was not opened.
2. **The official documentation** at `code.claude.com/docs`: *How Claude Code works*, *Settings*, *Permissions*, *Hooks*, *Subagents* and *Memory*, read on 2026-10-13.
3. **What is observable without opening the program:** `claude --help`, the layout of `~/.claude`, and the record types and field names (not the content) of one local session transcript.

The Claude Code binary was **not** decompiled, disassembled or unpacked. Anthropic's terms for Claude Code do not allow it, so this note stops at what is published or observable.

Labels used below:
- **[doc]** the documentation says so;
- **[sdk]** the SDK's types or code show it;
- **[obs]** seen on this machine;
- **[inf]** inferred from those;
- **[wisp]** Wisp's `main` at `226292d`.

---

## 1. Shape: one loop, many front-ends, one wire protocol

**Claude Code.**
- The docs describe the product as the harness around the model: it supplies the tools and manages what the model sees [doc]. The loop is gather context, act, verify, repeated until done; the model decides each step, and the person can interrupt or queue a correction that is read after the current tool calls finish [doc].
- The same loop serves the terminal, IDEs, the desktop app, the web, Slack and CI [doc]. Programs drive it as a subprocess: the SDK starts `claude --output-format stream-json --verbose` and exchanges two kinds of lines [sdk]:
  - messages: user, assistant, system, result and stream events;
  - `control_request` / `control_response` pairs.
- Control requests are `initialize` (registers hooks and agents), `can_use_tool` (a permission question sent to the host), `hook_callback`, `set_permission_mode`, `interrupt`, `rewind_files`, `mcp_message` (in-process MCP servers), `mcp_reconnect`, `mcp_toggle` and `stop_task` [sdk].
- A permission question carries the tool, its input, `permission_suggestions`, a `blocked_path` and, for a subagent, its `agent_id` [sdk]. The answer is allow (optionally with `updated_input` and permission updates to save) or deny (with a message and an optional `interrupt`) [sdk].

**Wisp.** A stateless core (`wisp/core/`) with in-process transports: CLI, TUI, WebSocket server and headless [wisp]. The desktop app talks to the server over WebSocket. The TUI and the REPL call the runtime directly.

**Difference.** Claude Code has **one external protocol** that every host speaks, so a feature such as "a background subagent's permission prompt reaches the person" is built once. In Wisp the same feature needed three wirings: REPL, server and TUI (PR #134).

---

## 2. Settings: five scopes, one precedence

**Claude Code** [doc]. Highest first:

| Scope | File |
|---|---|
| Managed | `managed-settings.json`, MDM, or the organisation console |
| Command line | `claude --settings` |
| Project, local | `.claude/settings.local.json` |
| Project, shared | `.claude/settings.json` |
| User | `~/.claude/settings.json` |

A key set higher overrides the same key lower. Permission rules, hooks, environment variables and MCP servers all live in these files. The SDK selects which ones load with `setting_sources: user | project | local` [sdk].

**Wisp** [wisp]: process environment > `~/.config/wisp/.env` > `~/.config/wisp/config.json` > defaults. A project contributes guideline files (`AGENTS.md`, `CLAUDE.md`, `.wisp/instructions.md`) and hooks (`.wisp/hooks`), but **no settings file**. There is no managed (organisation) scope.

**Difference.** A team cannot check Wisp permissions or settings into a repository, and one person's project-specific choices end up in the global config. PR #138 gives each user setting one owner; the project scope is still missing.

---

## 3. Permissions: a rule language with fixed precedence, and modes

**Claude Code** [doc]:

**Rules.**
- Rules are `allow`, `ask` or `deny` lists. Entries are a tool name, or a tool with a specifier: `Bash(git *)`, `Read(./.env)`, `Edit(src/**)`, `WebFetch(domain:github.com)`, an MCP tool name, or `Agent(Explore)`.
- **"Rules are evaluated in order: deny, then ask, then allow."** Specificity never changes the order, so an allow rule cannot carve an exception out of a deny rule.
- A bare-name deny rule removes the tool from the model's context entirely.

**Shell awareness.**
- Commands are split on `&&`, `||`, `;`, `|`, `|&`, `&` and newlines. An allow rule must match **every** part; a deny or ask rule fires if **any** part matches, including inside subshells, command substitutions and loop bodies.
- Output redirects and `tee` targets are checked against the `Edit` rules. Input redirects are checked against the `Read` rules.
- `Read`/`Edit` deny rules also cover `cat`, `head`, `sed` and similar commands, and a symlink to a denied file is itself denied.

**Modes.**

| Mode | What it does |
|---|---|
| `default` (Manual) | Asks on first use of each tool. |
| `acceptEdits` | Auto-accepts edits and common filesystem commands inside the working directories. |
| `plan` | Explores and proposes; no edits. |
| `auto` | A background classifier reviews actions instead of the person. |
| `dontAsk` | Auto-denies anything that would prompt. |
| `bypassPermissions` | Skips prompts, except protected paths such as `.git` and `.claude`. |

Managed settings can disable `bypassPermissions` and `auto`.

**"Don't ask again".** It is saved per repository for Bash and WebFetch, and per session for edits. A compound command is saved as one rule per subcommand.

**Wisp** [wisp]:

**Modes.** `read_only`, `auto_edit` (the default), `ask_all` and `full`.

**Execution path** (`wisp/tool_executor.py`), in order:
1. dangerous-command block;
2. policy hard-deny;
3. layered authority (denials only);
4. repeat-call guard;
5. fetch circuit breaker;
6. pre-tool hooks;
7. plan-mode guard;
8. permission-mode guard;
9. approval, where **no approver is not an approval**;
10. event-specific pre-hooks;
11. execute.

**Other controls.**
- The deterministic gates (`docs/harness/invariant-gates.md`) cover paths, commands, secrets, the dependency lock and the completion verifier.
- Narrow user-owned grants: `WISP_UNATTENDED_AUTO_APPROVE_TOOLS`.

**Same idea.** Claude Code's `dontAsk` is Wisp's "no approver is not an approval". Both fail closed when nobody can answer.

**Difference.** Wisp has no **user-writable rule language with a stated precedence**. What a person can allow or deny is spread across modes, environment switches and gate configuration. Claude Code's "deny, then ask, then allow; specificity never reorders" is one sentence a person can reason about, and its subcommand-wise matching is what makes `Bash(git *)` safe to grant. Not checked: whether Wisp's command gate splits compound commands the same way (`wisp/core/gates/`).

---

## 4. Hooks: many lifecycle events, a small exit-code contract

**Claude Code** [doc][sdk]:

**Events.** About 30, including:
- `SessionStart`, `UserPromptSubmit`;
- `PreToolUse`, `PermissionRequest`, `PermissionDenied`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`;
- `SubagentStart`, `SubagentStop`, `TaskCreated`, `TaskCompleted`;
- `Stop`, `StopFailure`;
- `PreCompact`, `PostCompact`;
- `PreModelSwitch`, `PostModelSwitch`;
- `FileChanged`, `CwdChanged`, `WorktreeCreate`, `ConfigChange`;
- `SessionEnd`.

**Handler types.** `command`, `http`, `mcp_tool`, `prompt` (a one-turn model judgement) and `agent` (a tool-using subagent, experimental).

**Contract.**
- The handler gets JSON on stdin: `session_id`, `transcript_path`, `cwd`, `permission_mode`, the event name, and the tool name, input and id for tool events.
- Exit codes:
  - **0** means success, and stdout is parsed as JSON if it is one object;
  - **2** means block on events that can block, with stderr as the reason (exit 2 blocks even if the JSON allows the action);
  - **any other code** is a non-blocking error.
- JSON fields:
  - `continue: false` stops the session;
  - `decision: "block"` with a `reason`;
  - `hookSpecificOutput.permissionDecision` (`allow`, `deny`, `ask` or `defer`) with an optional `updatedInput`;
  - `additionalContext` (capped at 10,000 characters).

**Matching and running.**
- Matchers are an exact name, an `A|B` list, or otherwise an unanchored regex. An `if` field filters further with permission-rule syntax.
- All matching hooks run in parallel, and an identical hook defined in two settings files runs once.
- Default timeouts are 600 s for command, HTTP and MCP hooks, 30 s for prompt hooks and 60 s for agent hooks. A timed-out hook does not block unless `onFailure: "block"` is set.

**Wisp** [wisp] (`wisp/infra/hook_types.py`):
- Events: `tool_call`, `tool_result`, `pre_tool_use`, `post_tool_use`, `pre_bash`, `post_bash`, `pre_file_write`, `file_write`, `session_start`, `session_end`, `subagent_spawn`, `subagent_complete`, `subagent_fail`.
- Results: allow or block.
- Hooks are configured in `.wisp/hooks` (project) and the config directory's `hooks` (user).

**Difference.** Wisp has no `UserPromptSubmit`, `Stop`, `PreCompact` or `PermissionRequest` events. Those four are where Claude Code users attach the controls Wisp builds in:
- "do not stop until tests pass" (a `Stop` hook);
- injecting context at the start of a prompt;
- saving state before compaction;
- answering permission prompts from outside.

Wisp also has no input-rewriting result (`updatedInput`) and no context-adding result (`additionalContext`).

---

## 5. Subagents: separate context, summaries back, bounded fan-out

**Claude Code** [doc]:

**Definitions.**
- Markdown files with YAML frontmatter (`name`, `description`, `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `skills`, `mcpServers`, `hooks`, `memory`, `background`, `isolation: worktree`, `effort`, …).
- Highest priority first: managed, `--agents` JSON, `.claude/agents/`, `~/.claude/agents/`, plugins.

**Context and results.**
- Each subagent runs in **its own context window** and sees only the delegation message, plus `CLAUDE.md` and a git status snapshot (the built-in Explore and Plan agents skip both).
- **Only its final report returns** to the parent, and the report is scanned for instruction-like text and marked as subagent output.
- Transcripts are kept and a subagent can be resumed by `SendMessage`.

**Limits.** Nesting goes up to 3 levels by default, and up to 20 run concurrently.

**Background subagents.**
- Their permission prompts appear in the main session, naming the subagent. Esc denies that single call without stopping the agent.
- They get a reduced tool set.
- A subagent's own `permissionMode` applies only when the parent is in `default`, `dontAsk` or `plan`.

**Wisp** [wisp]:
- `wisp/multi_agent/` runs subagents with depth 2 and branching 3 by default.
- In `auto_edit`, children never get `run_bash`, `spawn`, `fanout` or git/gh writes. The researcher reads untrusted web pages, so it gets search tools and no shell.
- Since PR #134, a child's approval request reaches the person through the turn that started it, in the REPL, the server and the TUI.

**Same idea.** Isolated context, a summary back, bounded depth, and a child's prompt reaching the human with the child named.

**Difference.** Claude Code agents are **user-defined files** and can be resumed later. Not checked for Wisp: whether a person can define a subagent in a file, and whether a finished subagent can be resumed.

---

## 6. Context: load little up front, the rest on demand

**Claude Code** [doc]:

**Instruction files.**
- `CLAUDE.md` files load from the managed path, the user's `~/.claude/CLAUDE.md`, and every directory from the working directory upward, at launch.
- Files in **subdirectories load on demand**, once Claude reads or edits a file there.
- `@path` imports go up to 4 hops deep.
- `.claude/rules/*.md` files can be **path-scoped** (`paths:` frontmatter), so they load only when matching files are in play.

**Auto memory.** Claude writes `MEMORY.md` plus topic files itself. The first 200 lines or 25 KB load at each start.

**On-demand loading.**
- **Skills:** only their descriptions are in context until one is used.
- **MCP tool definitions are deferred** and fetched through a tool search, so only their names cost context until one is called.

**Compaction.**
- It **clears older tool outputs first, then summarises**.
- `/compact <focus>` and a "Compact Instructions" section steer what is kept.
- It **stops after a few attempts** and shows an error when one huge output refills the window, instead of looping.

**Large tool output.** It is saved under the session folder (`tool-results/`), and the model gets a pointer and a preview [obs: this session's folder has `tool-results/` files, and their results said "Output too large … saved to"].

**Wisp** [wisp]:
- Guideline files: `AGENTS.md`, `CLAUDE.md` and `.wisp/instructions.md` from the workspace, plus `~/.config/wisp/CLAUDE.md`.
- Skills are injected into the prompt. The field log (O-17) measured about 12.6k tokens per request, against the about 1.7k that `wisp doctor` reports.
- Tool schemas: an 11-tool default profile is about 1.7k tokens; `full` is about 8.3k.
- Compaction:
  - the summariser model is empty by default, so compaction truncates and appends a harness-written record of files and test runs (`wisp/core/compaction_record.py`);
  - MCP tool output is cut at 8,000 characters (`tool_executor.py`), and the rest is lost.
- A tool-search branch exists but is not merged (`feat/tool-search`).

**Difference.** This is the largest, and the most measurable, difference:
- Claude Code pays for descriptions and names, and loads bodies and schemas when they are used.
- Wisp pays for skill bodies on every request and truncates large outputs, where Claude Code saves them to a file.

---

## 7. Sessions, transcripts and checkpoints

**Claude Code**:

**Transcripts.** Each session is an append-only JSONL file under `~/.claude/projects/<dir>/` [doc]. Each record has a `uuid`, a `parentUuid` (the conversation is a tree, which is what makes forks cheap), `isSidechain` for subagent branches, `sessionId`, `cwd` and `gitBranch` [obs]. Record types seen on this machine [obs]:
- `user`, `assistant`, `system` (with subtypes such as `compact_boundary`, `api_error` and `stop_hook_summary`);
- `attachment`, `queue-operation`, `mode`;
- `file-history-snapshot` and `file-history-delta`, which are the checkpoints;
- `cost-state` (total cost, durations, lines added and removed);
- `pr-link`, `custom-title`, `last-prompt`.

**Resume and fork.** `--resume` and `--continue` keep the session id; `--fork-session` copies the history into a new id [doc].

**Checkpoints.** Files are snapshotted before each edit, and Esc twice rewinds. Checkpoints are separate from git, and remote side effects cannot be undone [doc]. The SDK exposes `rewind_files(user_message_id)` [sdk].

**Programmatic access.** Hosts can mirror transcripts to their own store (`session_store`) and resume from it [sdk].

**Wisp** [wisp]: sessions are persisted, there is a `rewind` tool and `wisp/tools/checkpoints.py`, graph runs keep their own store, and an audit chain records decisions.

**Difference** [inf]: Claude Code's transcript **is** the event log: one file holds conversation, checkpoints, cost and queue state, keyed by a tree. Wisp keeps these in several stores. Not compared in detail here.

---

## 8. Sandbox

**Claude Code** [sdk][doc]:
- Optional OS-level bash sandboxing on macOS and Linux.
- `autoAllowBashIfSandboxed` (default on), `excludedCommands`, and `allowUnsandboxedCommands`, which the model uses through `dangerouslyDisableSandbox`.
- The network can be limited to Unix sockets and local binding.
- **Filesystem and network limits are derived from the permission rules** (`Read`/`Edit`/`WebFetch`), not configured twice.

**Wisp** [wisp]: a Docker sandbox image (`wisp-sandbox`, `WISP_SANDBOX`) and a deep doctor check that runs it.

**Difference.** Claude Code derives the sandbox from one rule set. In Wisp, the sandbox and the gates are configured separately.

---

## 9. Verification and honesty: where Wisp is ahead

**Claude Code** [doc][inf]: "verify results" is a phase the **model** chooses to do. The harness's own levers are:
- `Stop` hooks (a hook can refuse to let the turn end);
- `prompt` and `agent` hooks as judges;
- `--max-budget-usd` and `task_budget`;
- `--json-schema` for structured output.

Nothing in the documentation describes a built-in rule that a code-changing turn must end with a passing test run.

**Wisp** [wisp], all in the harness:
- the completion verifier and verification floor: a code-changing turn that ends unverified gets a harness-written UNVERIFIED line;
- reasoning-core rules R1 and R4, enforced by default;
- a graph verifier whose ALLOW must match a green `run_tests` it actually observed;
- an anchored test-summary reader, so a red run that prints green lines stays red (PR #139);
- the audit chain.

**Keep these.** They are Wisp's distinctive design, and Claude Code would need user-written hooks to get them.

---

## 10. What Wisp could take, ranked by payoff over cost

| # | Change | Why | Size |
|---|---|---|---|
| 1 | Load skill bodies and MCP or extra tool schemas **on demand** (descriptions and names up front; finish `feat/tool-search`). | The biggest measured cost: O-17, about 12.6k tokens per request. Claude Code's design is proof it works. | Medium |
| 2 | Save oversized tool output to a session file and give the model a pointer and a preview, instead of cutting at 8,000 characters. | Nothing is lost, and the model can read the rest when it needs to. | Small |
| 3 | A **permission rule language** with one stated precedence (deny > ask > allow), compound-command-aware matching, and a **project settings file**. | One thing a person can read and check into a repository, replacing scattered switches. | Large; needs a design doc and the owner's decision |
| 4 | Add `UserPromptSubmit`, `Stop`, `PreCompact` and `PermissionRequest` hook events, with an exit-2 or `decision: block` contract and `additionalContext`. | Lets owners attach their own controls without code changes. | Medium |
| 5 | Compaction: clear old tool outputs before summarising, and stop after N attempts when one output refills the window. | Cheaper than summarising, and bounded (rule 7: everything that loops has a bound). | Small |
| 6 | One control protocol for every front-end (REPL, TUI, server, desktop). | Features such as child approvals get built once instead of three times. | Large; architectural |

Not recommended:
- **Copying `auto` mode's classifier.** It is a trust decision for the owner. Wisp's current answer (no approver is no approval, plus narrow grants) fails closed.
- **Copying `bypassPermissions`.**

## Limits of this note

- It was not checked against the binary's behaviour, by choice (see the top).
- Documentation pages longer than 100k characters were read in part: *Hooks* (about 269k), *Subagents* (about 109k).
- For Wisp, "not checked" is written wherever I did not read the code: compound-command splitting in the command gate, file-defined subagents, subagent resume, and the session store layout.
