# Three tools Wisp lacks: deferred `tool_search`, `ask_user`, `schedule_wakeup`

Status: **design, 2026-10-11. Nothing here is built** (the deferred `tool_search` already exists on unmerged local branches; see section 2). It follows a comparison of Wisp's 54 tools with Claude Code's built-ins: of the sixteen, Wisp has six, one in part, and lacks nine. Six of the nine belong to the Claude app's own interface (hosted pages, install cards, sending files to a chat) and mean nothing in Wisp. Three matter to an agent harness, in this order of readiness: `tool_search` (built, unmerged), `ask_user` (a model-callable question to the human), `schedule_wakeup` (a self-set timer).

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

## 5. Order, size and what is out

| | state | size | depends on |
|---|---|---|---|
| `tool_search` | built, 5 commits, unpushed | merge, measure | the judge (fixed in #119, open) |
| `ask_user` | design | the executor seam, three transports, the REPL picker, tests | nothing |
| `schedule_wakeup` | design | store reuse, REPL timer, tests | background jobs P1 first |

Out, on purpose: the Claude app's hosted pages, install cards and file-to-chat tools (`Artifact`, `SearchPlugins`, `SuggestPluginInstall`, `SuggestSkills`, `SendUserFile`); `ReportFindings` (Wisp's `review` is a deterministic command, #118, not a model tool).

## 6. Decisions for the owner, after reading

1. **`ask_user` default:** `auto` (on wherever a human can answer; my lean) or opt-in.
2. **Confirm rule 1 for `ask_user`:** an answer never pre-authorises a gated call, and there is no scripted-answers file. If you want one for CI, it would be a separate, explicit grant.
3. **A wake-up that expired while the REPL was closed:** offer it (my lean) or drop it.
4. **Order:** the ready one first, as listed, or `ask_user` before the `tool_search` measurement.
