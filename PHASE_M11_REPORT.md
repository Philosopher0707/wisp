# PHASE M11 REPORT — A Node References Its Work Unit

Phase **M11** of the Persistent Graph Loop migration. Recorded as *"the graph
does not drive execution"* (P5 item 5) and re-scoped by M9 to its real
precondition: **node identity**. Report of record; decision is **ADR-0033**;
tests are `tests/test_node_identity.py` (24) plus the M9 tests this phase
updated.

## 1. What M9 found, verified once more

M9 §17.6 pinned the defect and named it M11's precondition:

> *"`build_turn_graph(run_id, n)` generates `turn:0…turn:n-1` from
> `len(exchanges) + 1`; a node never references its work unit."*

Reproduced before changing anything — one real turn, two tool exchanges, ids
`c0` and `c1`, `task_graph=True`:

| Observation | Before M11 |
|---|---|
| node ids in the persisted graph | `turn:0`, `turn:1`, `turn:2` |
| `c0` / `c1` present anywhere in it | **no** |
| `detail` on every node | `""` |

`WISP_TARGET_ARCHITECTURE.md` §14 says why that is not cosmetic: *"given the
journal, the system can reconstruct the state as of any recorded transition,
and **re-executing from that point is idempotent**."* Re-executing a work unit
idempotently requires naming it. An index cannot.

## 2. Finding F29 — the ratchet forbade the fix

M9's guard for its own property (the graph must not become a second copy of
the transcript) was a **name blacklist**:

```python
payload_fields = {"tool", "tool_name", "arguments", "args", "result",
                  "content", "text", "messages", "transcript",
                  "tool_call_id", "output"}
```

`tool_call_id` is in that set — and it is the only thing that can reference a
work unit. **M9's guard forbade exactly what M9's own report says M11
requires.** Two artifacts of one phase, contradicting each other. The
contradiction is the finding, and it is why the guard could not simply be
relaxed: the property is right, the mechanism was wrong.

## 3. Finding F30 — a name blacklist is evadable by naming

The same guard passes for a payload field called `body`, `payload`, `blob` or
`body_text`. It is a list of words, not a property, so it cannot close the
defect class it exists to close — it catches only the spellings its author
thought of. Verified: the replacement ratchet is driven against a synthetic
field the blacklist would have missed, and fails on it
(`test_the_payload_ratchet_is_superseded_and_strictly_stronger`).

## 4. The decision (ADR-0033)

1. **`TaskNode.work_unit: str`** — the identity of the work unit the node
   records. An *identity*, not content: `call:<protocol id>` for a closed
   tool exchange (ids joined by `+` for a batch), `output` for the terminal
   node.
2. **`node_id` stays `turn:i`, deliberately.** It is the graph's *structural*
   key — edges, `deps`, transitions and supersession all address it — so it
   must stay stable and unique within the graph. Deriving it from a
   provider-supplied id would put the topology at the mercy of transcript
   data (and of `_exchange_parts`'s random fallback for id-less traffic). Two
   fields, two jobs.
3. **`build_turn_graph(run_id, work_units)`** — it takes the identities, not
   a count. A node that records nothing is **not constructible from the
   turn's path** — the defect stated as a type. A bare `str` is refused
   explicitly: `str` *is* a `Sequence[str]`, so `build_turn_graph(r, "abc")`
   would otherwise build three nodes named after the characters.
4. **`turn_work_units(exchange_call_ids)`** — the ONE authority for "what are
   a turn's work units".
5. **The identity comes from the authority that mints it.**
   `_serialize_tool_exchanges` now returns `(events, exchange_call_ids)`,
   read back from the blocks `_exchange_parts` just built. Not recomputed —
   and that is a correctness requirement, not tidiness: an exchange whose
   events carry no id gets a fresh `uuid4` (`_exchange_parts`), so a second
   pass would mint a *different* id and the node would reference a work unit
   the transcript never recorded.
6. **The ratchet classifies fields, not names.** `NODE_FIELD_KINDS` gives
   every `TaskNode` field a kind — `STRUCTURAL`, `REFERENCE`, or `PAYLOAD` —
   and `node_field_violations()` reports unclassified, stale and payload
   fields. `PAYLOAD` is a declared kind with **no member**: the prohibition
   is expressible and enforced, and a new field is a test failure until it is
   classified deliberately.

## 5. Why the reference is not a payload

A payload is content the graph could reconstruct a message from; a reference
is an opaque handle that names a work unit and carries none of it. The test
that separates them is behavioural, not definitional:

- `test_the_graph_still_supplies_no_message_content` — the serialized graph
  of a real turn contains no tool name, no argument value, no result text
  (probes: `read_file`, `secret.txt`, `hunter2`, `password`).
- `test_the_reference_resolves_to_the_journal` — every exchange reference
  names a `tool_call_id` the journal actually recorded, so the graph can be
  traced back to the work it stands for.

## 6. A live observation worth keeping

`test_a_parallel_round_is_journaled_as_one_exchange_per_call` started life
expecting the `call:c0+c1` batch form — and failed, because the live engine
dispatches each call and streams its reply before the next call arrives, so
the sequence it emits is `callA replyA callB replyB` and `_group_exchanges`
closes after each reply. The batch form is still produced by `turn_work_units`
and pinned by a unit test; the live-path test now asserts the one-exchange-
per-call behaviour **and pins the ordering it depends on**, so a future
engine that batches its tool events changes the node count visibly rather
than silently.

## 7. What this does NOT do — and the tripwires that say so

**The graph still does not drive execution.** M11's original wording. Node
identity is the *precondition* M9 identified; flipping control is the change
ADR-0029 already recorded as the wrong strong reading (the graph cannot
project the transcript, so making it the driver means copying the transcript
into it). Tripwire: `test_the_graph_still_does_not_drive_execution` — AST-
based, so an explanatory comment naming the symbol cannot make a whole-file
grep match its own documentation (the P8 trap M12 hit).

**M13 is still open.** The graph can now say *which* work units completed;
`ProgressSignal` still counts them (`completed_nodes` / `total_nodes`), so a
stagnation verdict cannot distinguish "three different nodes finished" from
"the same node reported three times". Tripwire:
`test_progress_signals_still_count_nodes_rather_than_name_them`.

## 8. Item status

| # | Concern | Status | Evidence |
|---|---|---|---|
| 1 | M9's finding verified against a real turn | `COMPLETE` | reproduction, §1 |
| 2 | A node references its work unit | `COMPLETE` | `TaskNode.work_unit`; RED-first test |
| 3 | The reference resolves to the journal | `COMPLETE` | `test_the_reference_resolves_to_the_journal` |
| 4 | The reference carries no content | `COMPLETE` | behavioural content probe |
| 5 | A node cannot be built without an identity | `COMPLETE` | `build_turn_graph(run_id, work_units)` |
| 6 | The evadable ratchet replaced | `COMPLETE` | F29/F30; field classification, AST-ratcheted |
| 7 | The identity is the one authority's output, not a recomputation | `COMPLETE` | `_serialize_tool_exchanges` returns it |
| 8 | The graph still does not drive execution — pinned | `DEFERRED` | tripwire (ADR-0029) |
| 9 | M13's precondition surfaced — pinned | `DEFERRED` | tripwire |

## 9. Completion criteria

- [x] M9's finding reproduced RED-first, then fixed
- [x] The reference is the same id the transcript uses — no recomputation
- [x] The reference is content-free (behavioural probe) and resolvable (journal trace)
- [x] The payload ratchet is no longer evadable by naming (F29/F30)
- [x] Existing callers updated — none weakened; one M9 test inverted as its
      own comment said it would be
- [x] Reachability: the new symbols are driven from the live turn path and
      from tests (RULE 11)
- [x] **Zero new failures** — see §10
- [ ] `ruff` / `mypy` — not installed

## 10. Regression

**129, set-identical in both directions.** Two full-suite runs on the final
tree read 129 each; their intersection is the stable baseline's 129 with
**zero new, zero fixed, and zero flaky** — compared as *sets*, because this
phase's own regression check found that `comm` cannot be trusted here:

> **Method finding (F31):** the first `comm`-based comparison reported 6
> tests in *both* directions while the failure sets were byte-identical —
> the baseline had been sorted under a different locale's collation than this
> session's `sort` (`en_US.UTF-8` weights punctuation differently than `C`),
> and `comm` line-walks its inputs expecting a common order. The comparison
> is now set-based (`LC_ALL=C sort` on both, or a Python set diff), and the
> caveat is recorded in the memory README.

The six neighbour suites — `test_task_graph_materialization`,
`test_graph_mutation`, `test_execution_view_projection`,
`test_stagnation_detection`, `test_runtime_tool_history`,
`test_durable_layer_reachable` — run 225 passed with the only two failures
both present in the stable baseline (environmental: no `jsonschema`, so every
call is denied — F8).

## 11. Honest limits

- **The terminal node's identity is a constant** (`output`). It names the one
  terminal work unit within a run, which is all it needs to do; it is not a
  per-message identity, and it should not be mistaken for one.
- **This environment exercises only the reply-only grouping path** —
  `jsonschema` is absent, so every call is denied pre-dispatch and only
  replies stream (F8). The call-side id extraction is pinned by the unit
  tests and by construction (the ids are read from the same blocks either
  way), but not driven end to end with a real tool result.
- **The `+`-joined batch reference is not produced by the live engine** —
  §6. It is pinned by `turn_work_units` unit tests; if a future engine
  batches its tool events, the live-path test's ordering pin will fail first,
  visibly.
- **M13 still reads counts**, so the stagnation detector cannot yet use the
  identity. That is the next phase's input, and the tripwire keeps it from
  being forgotten.
