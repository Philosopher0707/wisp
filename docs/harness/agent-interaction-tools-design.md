# Three tools Wisp lacks, and talking while a turn runs: deferred `tool_search`, `ask_user`, `schedule_wakeup`, live input

Status: **design, 2026-10-11. Nothing here is built** (the deferred `tool_search` already exists on unmerged local branches; see section 2). It follows a comparison of Wisp's 54 tools with Claude Code's built-ins: of the sixteen, Wisp has six, one in part, and lacks nine. Six of the nine belong to the Claude app's own interface (hosted pages, install cards, sending files to a chat) and mean nothing in Wisp. Three matter to an agent harness, in this order of readiness: `tool_search` (built, unmerged), `ask_user` (a model-callable question to the human), `schedule_wakeup` (a self-set timer). A fourth item, added at the owner's request, is not a tool but the REPL itself: **talking to Wisp while a turn is running** (section 5).

## Rules all three obey (they come from rules Wisp already has)

1. **An answer is information, a search is visibility, a wake-up is a prompt: none is authority.** Approval stays where it is (`ToolExecutor` and the transport's `approve()`); `permission_mode`, `auto_approve` and the standing grants are untouched. "Visibility is not authorization" is already the deferred-tools ADR's decision 4; the same sentence is written into the other two.
2. **No capability is not a result.** `NO_APPROVER` already says "nobody could be asked" is a different fact from "the human said no". The same split: `NO_ASKER` (no human on this transport) is not an answer, `NO_CLOCK` (nothing will outlive this turn) is not a scheduled wake-up. The model gets a distinct refusal; nothing is guessed.
3. **A tool is offered only where it can work.** `core/tool_surface.py` already composes role, child-mode, profile and capability filters and says why a tool is absent; a transport that cannot ask or wake simply does not get the tool. Children (subagents, background agents, graph nodes) never get `ask_user` or `schedule_wakeup`: they have no human and no session to wake.
4. **Everything that loops has a bound** (questions per turn and session; pending wake-ups; a chain of wake-ups).
5. **Text is data.** Model-written question text is stripped of terminal escapes and secrets before it is shown or logged; an answer reaches the model labelled as the user's answer, and a wake-up reaches it labelled as set by the agent itself.
6. **Flag-gated, default off, rollout by measurement** (the `capability_filtering` / `WISP_DEFERRED_TOOLS` pattern), with the judge as the arbiter now that its score no longer depends on the machine (#119, open).

## 1. How each behaves where (summary)

| | REPL (`wisp repl`, interactive) | CLI one-shot (`wisp --print`, headless, `wisp converge`) | TUI, WebSocket and the app |
|---|---|---|---|
| `tool_search` | works; nothing to see except the schemas it loads (`wisp tools` shows loaded and deferred) | works the same; one extra model round trip the first time | works the same |
| `ask_user` | a question block on stderr, numbered options (the arrow-key picker when a TTY), free text under "Other", Ctrl-C cancels; typeahead paused while it waits | **not offered**; a stale call returns `NO_ASKER` | TUI: a modal. WebSocket: a `question_request` event, answered by a `question_answer` message; no client means `NO_ASKER` |
| `schedule_wakeup` | arms a timer; fires only at the idle prompt with an empty input line; kept on disk | **not offered**; a stale call returns `NO_CLOCK` (the process ends when the turn ends) | TUI: same as the REPL. WebSocket/app: not in the first slice |
| live input (section 5) | notes echoed with a number and withdrawable, `/btw` side questions, `/skip` for the running command | no stdin to type into; nothing changes | TUI and app: the same runtime calls from their own input widgets |

## 2. Deferred `tool_search`: finish what exists

**State (verified 2026-10-11):** an ADR (`docs/adr/2026-10-06-deferred-tool-schemas.md`) and three stacked local branches, **none pushed**: `feat/tool-search` (3 commits: the ADR, headless reporting of `overhead_tokens_per_call`, the deferred set and `tool_search`), `feat/tool-search-menu` (+1: the compact prompt menu, phase 2), `feat/tool-search-invariants` (+1: executable invariants and a fix; 5 commits in all). Flag `WISP_DEFERRED_TOOLS`, default off. `main` has only the unrelated code-search tools `search_symbols` and `search_codebase`.

**The case, from the ADR's measurement:** a bare `wisp --print "say hi"` carries 11,620 tokens of fixed overhead per call; skills are about 86% of it and each skill is paid twice (its description in the menu and again as a `skill__*` schema). Deferral puts extension tools (every `skill__*` and MCP tool) behind a name-only menu and one `tool_search(query, max_results)` call that loads the matches' schemas into the session.

**What remains, in order:**

1. **Push the stack as one PR** (it is branched from an older `main`): merge `main` in, run the three branches' tests, `ruff`, `mypy`, the pin tests (`tests/reliability`), a Linux run. No new code is needed for this step.
2. **Measure, do not estimate** (the ADR's decision 5): `overhead_tokens_per_call` off versus on, and `wisp judge run` on at least 3 repeats off versus on, comparing solve rate and false success claims. This is the step that was blocked while a judge score depended on the machine.
3. **State the prompt-cache effect.** Loading a schema mid-session changes the tool list the provider sees, which may invalidate a provider-side prompt cache. The ADR does not quantify it; the measurement should, for the providers that cache.
4. **Default on only if the measurement allows it** (the ADR's own criterion). Until then it is an opt-in flag.

**CLI/REPL:** no new interaction. `wisp tools` should print deferred names beside loaded ones; the REPL's `/tools` (if present) the same; `discovered_tools` lives in the session, so a resumed session keeps what it found.

## 3. `ask_user`

### Contract

```
ask_user(question: str,                       # at most 500 characters
         options: [{label, description?}] = [],   # 0 (free text) or 2 to 4, label at most 60 characters
         multi_select: bool = false,
         default: str | null = null)          # a label; used when the user just presses Enter
-> "User answered: <label>"            # or the labels, comma-joined
 | "User answered (free text): <text>" # at most 2,000 characters, cut with a note
 | "[Not asked: no human is available to answer. This is not an answer: decide with what you have, or end the turn and say what you need.]"   # NO_ASKER
 | "[Cancelled by the user at the question. Do not ask it again.]"                                                                            # CANCELLED
 | "[The question timed out without an answer.]"                                                                                              # ASK_TIMEOUT
```

Bounds: 3 questions per turn and 10 per session; past that the tool says the budget is used and the model must decide or finish. The tool description tells the model that **an answer never approves a tool call** (the next gated call still goes through `approve()`), that it must not ask what it can find out itself, and that the user's reply is returned to the model verbatim.

### Plumbing (it mirrors approval one-for-one)

| approval today | `ask_user` |
|---|---|
| `Transport.approve(tool_call) -> bool` (abstract) | `Transport.ask(request) -> Answer | None`, **not abstract**: the base returns `None` (cannot ask), so existing transports and test doubles keep working. Plus `Transport.can_ask`, default `False` |
| `ToolExecutor.execute(..., approval_handler=...)`; `None` means `NO_APPROVER` | `ToolExecutor.execute(..., ask_handler=...)`; `None` means `NO_ASKER`. The executor intercepts `ask_user` before generic dispatch (it needs the transport; `TOOL_IMPLS` functions do not have one) |
| `approval_request` event (UIs render it) | `question_request` event, with the same fields UIs need |
| `ApprovalCancelled`, `ApprovalTimeout`, `DENIAL_USER_DENIED` | `AskCancelled`, `AskTimeout`; reuse `DENIAL_CANCELLED`; two new codes `DENIAL_NO_ASKER`, `DENIAL_ASK_TIMEOUT`, registered where the others are (`core/events.py`) so the outcome classification stays total |
| `core/tool_surface.py` explains an absent tool | the same, with "no human on this transport" |
| audit log line for each approval or denial | an audit line for each question: asked, answered or not, the answer's kind and length, **not its content** |

New settings: `ask_user` (`WISP_ASK_USER`: `auto` | `on` | `off`, default `auto` = offered on transports with `can_ask`), `WISP_ASK_TIMEOUT_S` (default 0 = wait for a present human; the WebSocket path sets 600).

### REPL and CLI behavior in detail

- **REPL / `CLITransport`.** The question is drawn on stderr like the approval prompt, with numbered options and the default marked. On a TTY the existing arrow-key/Tab selector (`wisp/repl/picker.py`) is used; otherwise the line reader. The prompt takes **exclusive stdin** exactly as the approval prompt does (the typeahead reader that collects steering notes is paused, or the question loops forever, which is the bug that comment in `cli.py` records), and it reads through `asyncio.to_thread` so the event loop stays free. EOF or a closed stdin is `NO_ASKER`, not an empty answer. Ctrl-C at the prompt is `CANCELLED` for the question, not a killed REPL (the same distinction the approval prompt makes). The question and the answer are written into the session transcript so the next prompt knows what was asked.
- **One-shot `--print` / headless.** Not offered, so the model plans without it; a stale call returns `NO_ASKER`. With stdin piped there is still no human. A scripted-answers file (`--answers-file`) would let CI answer by question hash; it is **not** in this design: it would turn the tool into a way to pre-authorise, which rule 1 forbids.
- **`wisp converge` and the autonomous loop.** Not offered: the objective's acceptance check decides, not a human.
- **TUI.** A modal with the options, using the same asyncio-event bridge the TUI's `approve()` uses. **WebSocket/app.** `question_request` out, `question_answer` in, correlated by id; with no connected client, `NO_ASKER`, never the auto-approve environment variable (`WISP_WS_AUTO_APPROVE` has no meaning for a question).
- **Secrets and escapes.** Question text and option labels pass the secret scrubber and have terminal escapes removed before display and before logging. The user is told, once per session, that **what they type goes to the model**.

### Tests (RED first, through the production path)

The executor with and without a handler (distinct denials); each transport's `ask` (CLI with piped stdin, TUI via its event bridge, WebSocket with and without a client, headless); the typeahead pause (a steering note typed during the wait is not taken as the answer); Ctrl-C and EOF; every bound; sanitising; the tool absent from the provider's schemas when `can_ask` is false and for children; and a mutation probe over the handler plumbing and the bounds.

## 4. `schedule_wakeup`

### Contract

```
schedule_wakeup(delay_s: 60..3600, reason: str (at most 200), prompt: str (at most 500))
-> "Scheduled #3 for 14:05 (in 300 s): <reason>"
 | "[Not scheduled: this session ends when the turn ends; nothing will wake it.]"      # NO_CLOCK
 | "[Not scheduled: 3 wake-ups are already pending.]"                                   # bound
cancel_wakeup(id) -> "Cancelled #3" | "No pending wake-up #3"
```

Bounds: 3 pending per workspace, 20 per session, a chain of wake-ups (a wake-up turn that schedules the next) capped at 12. A wake-up turn spends the same token, iteration and cost budgets as any turn, through the same meter.

### Semantics

- **It is the agent's own earlier decision, delivered as a prompt, not a user instruction.** The turn it starts begins with `[scheduled wake-up #3, set by the agent at 13:55 for: <reason>]` and then the stored prompt. Approval policy applies as in any turn: a gated tool in `auto_edit` still needs an approver, and if the user is away the turn waits at that prompt; nothing is auto-approved because it was scheduled.
- **It fires only when the session is idle**: at the REPL's prompt with an empty input line. If the user is mid-turn it waits for the turn to end; if the user is typing, the toolbar shows "wake-up due: <reason>" and it fires when the line is empty or the user presses Enter on it.
- **A wake-up that expires while the REPL is closed is offered, never run.** At the next launch: "1 wake-up expired while you were away: <reason>. Run it? [y/N]". Fail closed on anything unattended and old.
- **Where it should not be used:** waiting on something Wisp itself started. Event-driven beats timers: with background jobs P1 (`run_in_background`) the completion notice wakes the next turn by itself. That is why this is built second.

### Mechanism

- Storage reuses `wisp/jobs/store.py` (atomic writes, workspace-bound ids, bounded, pruned): `<jobs root>/wakeups/<id>.json` with `due_at`, `reason`, `prompt`, `session_id`, `chain`, `state` (`pending | fired | cancelled | expired`).
- REPL: the idle prompt is prompt_toolkit's. In its `pre_run` the REPL arms `loop.call_later(due - now, ...)`, which calls `app.exit(result=<injected prompt>)` only when `buffer.text == ""`; otherwise it sets the toolbar notice. `/wakeups` lists and cancels; the footer shows the count.
- CLI one-shot: nothing is armed; the tool is not in the schema.

### Tests (RED first)

The bounds; `NO_CLOCK` on every transport that cannot wake; firing only when idle and empty; the typed-text case; the expired-on-restart offer (and that it is never auto-run); the chain cap; the label on the injected prompt; that a wake-up turn is metered; a pty test driving the real REPL with a short delay.

## 5. Talking while a turn runs (live input)

### What exists today (verified in the code)

- **Capture.** A background reader (`wisp/transport/typeahead.py`: stdin polled with a 50 ms `select`, POSIX terminals only, silently off elsewhere) collects lines typed during a turn. Each complete line goes to `runtime.inject_steering(session_id, text)`, a thread-safe queue.
- **Delivery.** `stateless.py` drains the queue **at each tool boundary** (after the round's tools have all finished) and appends each note as a user-role steering message plus a `steering_feedback` event, so the next provider call can change course.
- **Leftovers.** Notes the turn did not consume run as follow-up prompts once it ends ("N prompt(s) typed ahead"); a half-typed line is put back into the next prompt.
- **Interrupt.** Ctrl-C calls `turn_control.request_cancel(turn_task)`: the **whole turn** is cancelled, with a resume hint; the REPL survives.
- **Exclusive stdin.** The approval prompt pauses the reader and takes stdin; so would `ask_user`.

### The gaps

1. **A note is invisible and final.** Nothing says it is queued, what number it is, or when it was delivered, and it cannot be withdrawn. It also waits for every tool of the running round, so a note typed during a ten-minute `run_bash` is read after the ten minutes.
2. **No side channel.** A question is answered only after the turn, as a follow-up prompt.
3. **Interrupt is all or nothing.** To stop one runaway command the user must cancel the turn and lose the model's plan.
4. **Slash commands typed ahead are untested here.** What the reader does with `/something` typed mid-turn (queued as a steering note, or held for the dispatcher) was not traced; the router below makes it explicit and the tests pin it.

### Design: a line router, three behaviours, one flag

`WISP_LIVE_INPUT` = `auto` | `on` | `off` (default `off` until the pty tests have run, then `auto`: on where the reader works). A pure function `parse_live_line(line) -> LiveCommand` in `wisp/cli/live_input.py` classifies each captured line, testable with piped input like `prompt_for_approval`:

| typed during a turn | does |
|---|---|
| plain text | a steering note, as today, **plus an echo**: `↳ note 2 queued: <first 60 characters> (delivered at the next step; /unsend 2 withdraws it)`; the existing `steering_feedback` render gains the number |
| `/unsend [n]` | withdraws note `n` (default: the last) if it is not yet delivered |
| `/btw <question>` | the side question below |
| `/skip` | stops only the running command(s); the turn continues |
| any other `/command` | **held until the turn ends**, never shown to the model; the echo says so |

If the turn ends with notes undelivered, they run as follow-up prompts as today, after the line "2 notes were not delivered; running them now".

**Side question (`/btw`).** One separate provider call, with **no tools**, on a **copy** of the session messages taken at capture time, under a fixed preface ("answer briefly from the conversation so far; you cannot act; the main task continues"). It is printed as its own block (`┄ side answer ┄`) through the renderer's lock so it cannot split a streamed line, and it is **not appended to the session**: the main agent never sees it unless the user says so in a note. It cannot act (no tools), so text in it is data like any model text. Bounds: 600 output tokens, one per 5 s, five per turn, counted by the spend meter; `WISP_BTW_MODEL` may name a cheaper model (same provider, like the graph tiers). Risk, stated: on a single local model server (Ollama on one GPU) the side call competes with the main turn; on such a provider it queues behind the in-flight call instead of running beside it.

**Targeted interrupt (`/skip`).** The executor keeps the set of tool tasks running in the current round; `/skip` cancels those tasks (the same thread-safe `request_cancel`, once per task) and each returns `[Skipped by the user]` as its result, with a new outcome code registered beside `DENIAL_CANCELLED`, so the model sees what happened and continues. `bash.py` already handles `CancelledError`. **The hard part is the process, not the task**: the background-jobs lesson records that `run_bash_confined` cannot be killed from outside (the PTY tier's worker thread survives a cancel; the Docker tier leaves the command running when the exec client dies). So `/skip` is only honest if it kills the process tree with the jobs helpers (`wisp/jobs/procs.py`: descendants captured *before* cancelling, then SIGTERM then SIGKILL). Until then `/skip` is offered on the host tier only and says so. Ctrl-C keeps its meaning (cancel the turn); it is not redefined.

**Where.** REPL on a POSIX terminal: all of it. One-shot CLI: no stdin to type into, so nothing. TUI and app: the same three runtime calls (`withdraw_note`, `side_question`, `skip_running_tools`) from their own input widgets; the WebSocket gets `steer`, `unsend`, `side_question` and `skip` messages. While `ask_user` or an approval owns stdin, typed lines are that answer, not live input.

### Slices, each with a first caller

1. **Router, echo, numbering, `/unsend`, held slash commands.** No new model calls; fixes gaps 1 and 4.
2. **`/btw`.** Fixes gap 2; needs the renderer lock and the spend meter hook.
3. **`/skip` with the tree kill.** Fixes gap 3; depends on the jobs process helpers and is the only slice that can leave a process behind if wrong, so it has a mutation probe and a test that a real `sleep 60` is gone afterwards.

### Tests (RED first)

`parse_live_line` over every row of the table, including `/BTW`, extra spaces, an empty argument, a 5,000-character note (cut with a notice) and the 20-note queue bound; the echo and numbering; `/unsend` before and after delivery; a note typed during a slow tool is delivered at the boundary and the echo says so; `/btw` leaves `session["messages"]` byte-identical, runs while a scripted slow tool is in flight, and respects the bounds; `/skip` with nothing running says so, and with a real `sleep 60` the process tree is gone and the model's next round sees the skipped result; Ctrl-C still cancels the turn; a pty test drives the real REPL through all of it; a regression test that typed-ahead behaviour is unchanged with the flag off.

## 6. Order, size and what is out

| | state | size | depends on |
|---|---|---|---|
| `tool_search` | built, 5 commits, unpushed | merge, measure | the judge (fixed in #119, open) |
| `ask_user` | design | the executor seam, three transports, the REPL picker, tests | nothing |
| `schedule_wakeup` | design | store reuse, REPL timer, tests | background jobs P1 first |
| live input | design, 3 slices | slice 1 is small (a parser, echo, numbering); `/btw` needs the renderer lock; `/skip` needs the tree kill | slice 3 on the jobs process helpers |

Out, on purpose: the Claude app's hosted pages, install cards and file-to-chat tools (`Artifact`, `SearchPlugins`, `SuggestPluginInstall`, `SuggestSkills`, `SendUserFile`); `ReportFindings` (Wisp's `review` is a deterministic command, #118, not a model tool).

## 7. Decisions for the owner, after reading

1. **`ask_user` default:** `auto` (on wherever a human can answer; my lean) or opt-in.
2. **Confirm rule 1 for `ask_user`:** an answer never pre-authorises a gated call, and there is no scripted-answers file. If you want one for CI, it would be a separate, explicit grant.
3. **A wake-up that expired while the REPL was closed:** offer it (my lean) or drop it.
4. **Order:** the ready one first, as listed, or `ask_user` before the `tool_search` measurement.
5. **Live input:** should side answers stay out of the session (my lean, so the main task cannot be hijacked), or be kept as context? Should `/btw` use the session model or a cheaper `WISP_BTW_MODEL`? Commands are typed lines (Enter needed) because the reader is line-buffered; a single-key shortcut would need raw mode and a different design. Start with slice 1 only?
