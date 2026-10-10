# Wisp's graphs against "Graph Engineering: Stop Chaining Your Agents" (2026-10-11)

The owner asked for this article to be read and Wisp checked against it. This page records what was checked, with the evidence for each answer, one defect that is fixed in the same change, and four findings that are **not** changed here because each is a decision rather than a bug.

## 1. The source, and how much weight it gets

`x.com/seeconvm/status/2088199384580108339` (fetched through a public mirror of the post; x.com itself would not load): an article by `@seeconvm` ("trading data, not emotions / crypto // ai // polymarket", 2.3 million views), dated 2026-08-14. Its subject is Claude Code's "dynamic workflows" (an orchestration script with `parallel()`, `pipeline()` and `agent()`), and it gives 14 steps for turning a linear agent into a graph. The product claims (those API names, `ultracode`, `/deep-research`, the Bun port anecdote) are **not verified here**; they do not matter for Wisp, which has its own engine. The engineering claims are generic and checkable by reasoning, which is what was done. This is a secondary source, in the same class as the secondary Karpathy reporting in `repl-converge-design.md`.

Its three summary rules: (1) cut the arrows that carry no data, (2) default to a pipeline instead of a barrier, (3) when looping until nothing new turns up, dedupe against everything seen and not only against what was confirmed.

## 2. The check, step by step

Labels: **observed** (read in the source or run), **inferred**, **unknown**.

| Article step | What Wisp has | Label and evidence |
|---|---|---|
| 1-2 nodes are jobs, edges are data | `NodeContract` (input, output and failure schema, tools, budget, timeout), edges with a `reason` and a `mapping` | observed: `wisp/graph/types.py`, `coding_graphs.py` |
| 3 a contract per node, validated output | `output_schema` is passed to the subagent, which validates and retries a bad parse | observed: `graph/runner.py:59`, `multi_agent/subagent_orchestrator.py:1489-1517`. The templates only declare `{"type": "object"}`, so the validation is weak in practice |
| 4 the edge is a data contract | edges carry `mapping`; **two edges could write the same input key** | observed, **defect, fixed here** (section 3) |
| 5-6 fan out, fan in at a barrier only when needed | `max_concurrency`; six join policies (`all`, `any`, `quorum`, `min_success`, `best_effort`, `streaming`); the scheduler fails closed to a full barrier | observed: `graph/types.py` (`JoinPolicy`), `scheduler.py:83,100`. No coding template uses a non-default join, so every fan-in is a barrier |
| 8 route the edge in code | `ROUTER` nodes and a deterministic template picker; the strategy gate routes in host code | observed: `coding_graphs.py:pick_template`, `wisp/coding.py`. The `merge` nodes are routers |
| 9 a verifier on the edge | `VERIFIER` nodes (`ALLOW`, `REJECT`, `RETRY`, `ESCALATE`) | observed. The verifier is **one model reviewer**; there is no adversarial majority or lens-per-reviewer pattern. Both would still be model judgement, so they raise confidence without being proof |
| 10 isolate nodes, contain failure | worktree isolation for writers (`parallel_implement_merge` runs two implementers on isolated copies and merges); failures are values, not exceptions | observed for isolation. **Containment is not observed**: section 4 |
| 11 a cycle that converges | bounded `CycleSpec` (`max_iterations`, 3 in the templates); the objective-level loop in `core/convergence.py` is bounded by attempts and has a ladder that never repeats a rung | observed. "Loop until dry, dedupe against everything seen" does not exist in the graph engine; the closest thing is the ladder's history, which does dedupe strategies against everything tried |
| 12 tier the models | `ModelPolicy` (`model_class` cheap, standard or strong, `model`, `provider`) on every node contract, validated against `allowed_models` | observed, **but never executed**: section 5 |
| 13 topology is cost and latency | `STREAMING` join exists in the scheduler; nothing uses it | observed |
| 14 let the model draw the graph | `planner.py`: a model proposes an IR that deterministic code compiles and validates; the planner is declared untrusted | observed: `graph/planner.py:3-5`. Coordination is Python code, so it costs no model tokens, which is the article's point |

## 3. The defect: a fan-in that kept one branch of four

**Observed by running it.** `parallel_repo_analysis` (split, four read-only analysers, synthesize) was run through the real `GraphExecutor` with the host's function bindings and a scripted runner. With every branch succeeding, `synthesize` received `{"findings": "findings from tests"}`: the findings of **one** branch. All four edges into `synthesize` mapped `findings` to `output.findings`, and `_derive_inputs` (`graph/executor.py:807-820`) assigns each edge's value into one dict in edge order, so the last edge replaced the other three. Nothing failed, nothing was logged, the run succeeded, and the synthesizer was asked to summarise a repository from a quarter of the analysis.

Scan of the other four templates and the reference graph: no other collision.

**Fix.** (a) `parallel_repo_analysis` maps each branch to its own path (`findings.structure`, `findings.symbols`, `findings.deps`, `findings.tests`), so `synthesize` receives one `findings` object keyed by branch. (b) `validate_graph` has a new rule, `_validate_fan_in`: two unconditional edges into one node may not write the same input, or one input and a path under it (`x` and `x.y`), whichever order they come in. Conditional edges are exempt (they belong to lanes of which one is taken). A model-proposed graph that does this is now rejected at validation, with both sources named, instead of silently losing data.

**Evidence.** `tests/test_graph_fan_in.py` (20 tests): the real executor delivers all four branches, attributed; the validator rejects equal, prefix and reverse-prefix collisions, collisions between edges that are not neighbours, and two edges from one source; it accepts distinct keys, sibling paths, string-prefix names that are not path prefixes (`find` and `findings`), conditional lanes and edges without a mapping; every registered template and the reference graph validate. RED first (5 failed for the right reasons, after a first draft with an invalid topology was corrected). Mutation probe, 10 mutants (rule not called, conditional edges not exempt, equality only, string prefix, asymmetric prefix, only neighbouring pairs, sources not named, overlap not shown, template mapping reverted, conditional guard): all killed; two survived the first run and each produced a test or a simplification (an equivalent guard was removed). 1,019 graph, security and optimizer tests pass.

## 4. Finding, not changed: one failed branch discards the others' work

Run with `deps` failing: the other three branches succeeded, `synthesize` was **skipped**, and the run finished as `cancelled` with an empty `error`. `graph/executor.py:535` maps "a sink that did not run and is not benign" to `CANCELLED`, so an upstream failure reads as a cancellation nobody asked for. The article's rule (design every fan-in to tolerate missing inputs) is what the reference graph does (`reference.py:37`, a `join` with `best_effort`); the REPL's coding templates do not. The REPL's own summary names the failed node's message (`coding.summarize_graph`), so the failure is not silent, but the three finished analyses are thrown away.

Not changed, because it is a decision: a `best_effort` join makes a partial answer possible, and a partial answer presented as complete would break "missing is not positive". Doing it properly means the synthesizer is told which branches are missing, or the harness writes the coverage line itself and refuses to call a partial run a success. The run-status mapping (a skipped sink behind a failed branch is `FAILED`, not `CANCELLED`) is a separate change with a wider blast radius (store, traces, tests).

## 5. Finding, not changed: a node's model is validated but never used

`ModelPolicy.model`, `.provider` and `.model_class` are parsed (`graph/dsl.py`), checked against the graph policy's `allowed_models` and `allowed_providers` (`graph/validator.py:270`), counted by the optimizer, and then dropped: `SubagentNodeRunner` builds the `SubagentContract` without a `model` (`graph/runner.py:59`), and the contract's own field is honoured downstream (`multi_agent/_runner.py:992`, `contract.model or parent_config.model`). No code maps `model_class` to a model at all. Every node runs on the session model, so the "cheap models for the fan-out, a strong one for the merge" lever does not exist, and `tests/security/test_graph_authority.py::TestModelProviderConfinement` pins the confinement check without anything to confine.

Not changed, because the planner is a model and is declared untrusted (`graph/planner.py:3-5`): honouring a node's `model` lets a model-authored graph choose which model runs. Options, for the owner: (a) leave it inert, and say so in the validator; (b) honour an explicit `model` only when the graph policy has a non-empty `allowed_models` that contains it (fail closed), and add a `model_class` to model mapping that the owner configures (which models count as cheap or strong is the owner's call); (c) honour it for host-authored templates only.

## 6. What was not adopted, and why

- **Adversarial majority verification.** N skeptic agents voting multiply the cost, and the vote is still one model family judging itself. Wisp's stronger answer for code is the objective-level loop, where the verifier is the harness running the tests (`repl-converge-design.md`). A model verifier on the graph path remains the weak link.
- **`parallel()` and `pipeline()` as new primitives.** The engine already has join policies, including a streaming one; the gap is that nothing selects them, not that they are missing.
- **Letting the model write the orchestration.** Wisp's planner already proposes an IR; the open question is authority (section 5), not capability.

## 7. Applying the article's third rule to the objective loop

"Dedupe against everything seen" has a counterpart in the loop built in #114: the stagnation witness compares an attempt's measurement with the **kept** state only. An attempt that was reverted and is later repeated is detected as a regression each time, but is not recognised as "already tried". The ladder does change the rung on each attempt and the next attempt is told what was reverted, so the cost is bounded by the attempt budget. Recording every measurement digest and treating a repeat as `REPEATED` would be a small addition; it is noted here and not built, because no run has yet shown the repetition.
