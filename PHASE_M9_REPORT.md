# PHASE M9 REPORT — The Execution View: Faithful, and a Shape Not a Payload

| Field | Value |
|---|---|
| Item | **M9** — recorded as the migration's keystone |
| Predecessors | P1 (the journal), P4 (the materialized graph), M2 (journal-first reconstruction) |
| Status | **`COMPLETE`** — closed as *faithfulness + a corrected prerequisite*, not as an inversion |
| Decision | `WISP_ARCHITECTURE_DECISIONS.md` **ADR-0029** |
| Files changed | 2 production, 1 test file updated, 1 test file added |
| Tests added | `tests/test_execution_view_projection.py` (**11**) |
| Rollback | none needed — one branch in `Session.apply`, plus tests |

---

## 1. What M9 was recorded as

`WISP_MIGRATION_STATUS.md`: *"The message list is not yet a projection of the graph. P4 **enables** it
(both journal from one log); it is not implemented. Inverts the dependency between the message list and
the graph."*

`CONTEXT.md` went further: *"**M9 is the sole keystone.** M11–M15 are all the same change ("make the live
turn loop use the mechanism") and all are blocked on it."*

So the phase had a large claimed blast radius. Reconnaissance checked it, and the claim did not survive
contact — in two directions at once.

---

## 2. The target architecture says something narrower than the ledger did

`WISP_TARGET_ARCHITECTURE.md` §14:

```
Immutable Event Journal  ──materialize──►  Graph State  ──project──►  Execution View
```

> - The journal is the source of truth for *what happened*.
> - Graph rows are the materialized state for *what is true now*.
> - The message list and UI are **views** projected from **both**.

The ledger compressed "projected from **both**" into "a projection of the graph". That is a different
claim, and it is the one that does not hold.

---

## 3. Finding 1 — the graph cannot project the transcript

`TaskNode` carries:

```
node_id, kind, iteration, status, ready, deps, detail, superseded_by
```

No tool name. No arguments. No result. No assistant text.

Verified **behaviourally** as well as structurally — a real turn's graph, serialised:

```json
{"node_id": "turn:0", "kind": "agent", "iteration": 0, "status": "pending",
 "ready": true, "deps": [], "detail": "", "superseded_by": ""}
{"node_id": "turn:1", "kind": "agent", "iteration": 1, "status": "pending",
 "ready": false, "deps": ["turn:0"], "detail": "", "superseded_by": ""}
```

There is nothing in it that could become a message.

To make the graph the *source* of the transcript, the transcript would have to be copied into the nodes.
That is a second copy that can disagree with the first — the defect class this migration exists to
remove, and exactly what P2 warned about when it made `PROPOSAL`/`OUTCOME` audit-only: *"a second path
into `messages` would duplicate every tool reply on replay."*

**So the strong reading of M9 is not just unimplemented, it is the wrong target.**

## 4. Finding 2 — the nodes are a count, not an identity

`build_turn_graph(run_id, n)` generates `turn:0 … turn:n-1` from `_work_units = len(exchanges) + 1`. A
node does **not** reference the exchange it stands for.

So the graph is a lower bound on iterations, arranged in a linear chain — not a structure *over* the
work. **This, not payload, is the real precondition for M11.** For the graph to drive execution, a node
has to be an identified unit of work; today it is an index.

That correction is the phase's most useful output, because it changes what M11 is.

## 5. Finding 3 — the projection that does exist is not faithful

`runtime.py` states the invariant at the construction site:

> *"replay replaces the live transcript, so the log has to reproduce `messages` exactly."*

**It did not.** The two producers of a tool reply disagreed by one key:

| Producer | Shape |
|---|---|
| `_exchange_parts` (live) | `{role, tool_call_id, content}` |
| `Session.apply` (replay) | `{role, content, name, tool_call_id}` |

Two consequences, both verified rather than reasoned about:

| Consequence | Evidence |
|---|---|
| the journal and the **blob** disagreed on the same session | `blob["messages"] != replay.messages`, so `reconstruct()` returned different messages depending on which source it chose |
| a **consumer branched** on the divergence | `context_pruner._get_tool_name_for_result` reads `name` first: live took the `tool_call_id` fallback, replay short-circuited |

The divergence never reached a provider — `providers/openai.py` normalises tool messages to
`{role, tool_call_id, content}` — so it was **latent rather than broken**. Latent is the point: two
producers of one structure, and the next consumer to read `name` inherits a difference no test covered.

### The RED-first test

`test_the_journal_reproduces_the_transcript_exactly` drives a real turn and compares the live transcript
against its replay. Written first, it failed with:

```
At index 2 diff: {'role': 'tool', 'content': '…', 'name': 'read_file', 'tool_call_id': 'c0'}
              != {'role': 'tool', 'tool_call_id': 'c0', 'content': '…'}
```

Five tests failed, all on that one key. All five pass now.

---

## 6. The decision (ADR-0029)

1. **The graph stays a shape.** It is not given transcript payload. `test_task_nodes_carry_no_transcript_payload`
   is a ratchet: it fails if a payload field appears on `TaskNode`, so the decision cannot be reversed by
   a convenience change.
2. **The transcript projects from the journal** — which is what the target architecture already says.
   The graph contributes status and structure, not content.
3. **Faithfulness is now an invariant with a test.** `Session.apply` produces the live shape exactly;
   the equality is asserted against a real turn, and the shape is asserted directly.

**Why the live shape wins.** It is what the engine has always produced and what the blob stores, so
aligning the replay changes one branch instead of changing what every consumer of `messages` sees. `name`
is also redundant in the message — it is in the assistant's `tool_calls` block, in `Session.tool_calls`,
and resolvable from `tool_call_id` — and adding it to the live path would put a key into `messages` that
every provider adapter strips again.

### One pre-existing test updated, and why it is not a weakening

`test_durable_layer_reachable.py::test_tool_result_without_id_keeps_legacy_shape` pinned the old shape.
Its docstring states a real requirement — *"Pre-migration events carry no tool_call_id; replaying them
must not invent one"* — but its assertion also pinned `name`, which is incidental to that purpose. The
assertion now names only the protocol keys and **adds** `assert "tool_call_id" not in s.messages[0]`, so
the test's stated requirement is asserted more precisely than before, not less.

---

## 7. Completion criteria

- [x] The strong reading of M9 checked against the repository and corrected — **ADR-0029**
- [x] The projection that exists is faithful, and pinned by a test on a **real turn**
- [x] The shape is asserted directly, not only via equality with a transcript that could drift with it
- [x] The graph's payload-lessness is a **ratchet**, not a comment
- [x] The real precondition for M11 identified and recorded (**node identity**)
- [x] **Zero new failures** — see §8
- [ ] `ruff` / `mypy` — not installed

## 8. Regression

Verified against the **stable baseline** (`.workbuddy-ai/memory/baseline-failures-stable.txt`).

| Comparison | Result |
|---|---|
| Count | **129** |
| New vs the stable baseline | **none** (`comm -13` empty) |
| Absent vs the stable baseline | **none** (`comm -23` empty) |

This phase changed the shape of a replayed tool message — a behaviour change on a path the suite
exercises — so the byte-identical set is meaningful here rather than expected: it says nothing else in
the tree depended on the extra key. The one pre-existing test that did is updated above.

Neighbouring suites re-run explicitly: **448 passed** across the fifteen migration suites.

`ruff` / `mypy`: not installed.

---

## 9. What this does to M11–M15

The ledger recorded all five as blocked on M9. That was partly wrong, and correcting it is worth more
than the phase itself:

| Item | Recorded | Actually needs |
|---|---|---|
| **M11** | blocked on M9 | **node identity** — the graph's nodes must reference their work unit. M9 does not unblock it; M9 *redefines* it |
| **M12** | blocked on M9 | nothing from M9 — the recovery ladder needs a hook on the failure path |
| **M13** | blocked on M9 | meaningful progress signals, which depend on M11 |
| **M14** | blocked on M9 | nothing from M9 — the context assembler must build `ContextItem`s |
| **M15** | blocked on M9 | nothing from M9 — the spawn site must pass a `child_principal` |

So "one coherent next step" was itself an overstatement. **M12, M14 and M15 are independent of the graph
and of each other**, and are each bounded. M11 and M13 genuinely share a prerequisite, and it is node
identity.

---

## 10. Honest limits

- **The graph is still not drivable.** Its nodes remain indices. ADR-0029 records what would change that;
  it does not do it.
- **The faithfulness invariant is asserted for the cases the suite can produce here.** `jsonschema` is
  absent, so every tool call is denied — the denied path is covered (and is in fact the default path in
  this environment). A *successful* tool execution is exercised only through the synthetic paths in
  `test_durable_layer_reachable.py`, not end to end. The invariant is shape-level, so this is a smaller
  gap than it sounds, but it is a gap.
- **No provider was contacted.** The claim that the extra key never reached one rests on reading
  `providers/openai.py`'s normalisation, not on observing a request.
- **M9 closes without the inversion the ledger described.** That is the finding, not a shortfall — but it
  does mean the migration's remaining work is larger than "one keystone", not smaller.
