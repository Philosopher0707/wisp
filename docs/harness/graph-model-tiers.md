# Graph model tiers: which model a `cheap`, `standard` or `strong` node runs on

Status: built 2026-10-11. Setting `graph_model_tiers`, environment variable `WISP_GRAPH_MODEL_TIERS`. **Empty by default, and then nothing changes**: every graph node runs on the session model, exactly as before.

## What it is

A graph node says how much model it deserves: `ModelPolicy.model_class`, one of `cheap | standard | strong` (the graph DSL refuses anything else at compile time). Until this setting the label was ignored when the graph ran: `SubagentNodeRunner` built the subagent without a model (verified on `main`), so every node used the session model. The setting is the **operator's map** from the label to a real model.

## Setting it later

Any of these, whichever is easier. The environment wins over the file, and Wisp reads the setting when it starts, so relaunch after changing it:

```
wisp config --set "graph_model_tiers=cheap=<a small model>,strong=<your best model>"
wisp config --validate
```

```
export WISP_GRAPH_MODEL_TIERS="cheap=<a small model>,strong=<your best model>"
```

```json
{ "graph_model_tiers": "cheap=<a small model>,strong=<your best model>" }
```

(the last is `~/.config/wisp/config.json`, which `wisp config --set` writes). Rules:

- Pairs separated by commas; the tier is `cheap`, `standard` or `strong` (any case); a tier left out means the session model.
- A model is a model **of the session's provider**. A tier is another model name for the same provider, not another provider: no second endpoint or key is involved.
- A model name starts with a letter or a digit, then letters, digits and `. _ : / @ + -`, at most 128 characters, no `..`. Names such as `nvidia/nemotron-3-ultra-550b-a55b` or `qwen2.5-coder:7b` pass.
- A malformed entry is **dropped, never repaired**, and counted in the log without quoting it (whatever was typed there might have been pasted by mistake): that tier then uses the session model. An unknown tier name is dropped the same way.
- `standard` is the tier of every node that is not tagged, so setting it moves all of those nodes off the session model.

## Which nodes are tagged

Only the unambiguous cases. The write path is never tagged, so setting `cheap` or `strong` cannot change what writes code; setting `standard` moves every untagged node, the write path included, which is why it is the operator's explicit choice.

| template | `cheap` (independent read-only workers) | `strong` (the node that decides from them) | untouched |
|---|---|---|---|
| `parallel-analysis` | `structure`, `symbols`, `deps`, `tests` | `synthesize` | |
| `complex` | `arch`, `deps` | `plan` | `analyze`, `implement`, `test`, `review`, `final` |
| `simple`, `repair`, `parallel-implement-merge` | | | all nodes |

## What a graph cannot do

- **The graph, its planner and every node input can pick a tier, never a model.** A model or provider named inside a graph is still validated against `allowed_models` and `allowed_providers` and still never reaches the subagent; a node input called `model` or `model_class` is never read. Tests pin each of these.
- `allowed_models` constrains what a graph may *name*. The operator's map is the operator's own choice and is not narrowed by it.
- A map that cannot be read (a corrupt config file) means no tiers, with a warning; it never stops a graph.

## Seeing what ran

`results_by_node[node]["model"]` now holds the model each node was asked to use (the session model for a node with no tier mapped); it was always empty before, because the runner read an attribute no one sets. `/graph trace` prints it after the duration, for example `✓ structure  1.2s  (qwen2.5-coder:7b)`. The test with the real executor, the real `SubagentOrchestrator` and the mock provider reads the configuration the real runner built for each child and finds the tier's model in it.

## Limits

- Same provider only.
- A wrong model name is handled the way the provider handles any unknown model for any node (an error from the provider, or, for a provider that pulls on demand, a pull). There is **no silent fallback to the session model**: that would hide the typo and double the cost.
- The tags live in `wisp/graph/coding_graphs.py`; there is no per-node override and no per-template tier table in the setting.
- `wisp doctor` does not list the map yet.
- `SubagentResult.model_used` exists and nothing sets it, so the node's model is what the runner asked for, not what the provider reported back.
