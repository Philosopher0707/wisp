"""The Loop layer — step, budget, failure taxonomy.

One of the seven layers this project is organised by:

| Layer   | Concern |
|---------|---------|
| Model   | `wisp/providers/` — protocol + adapters |
| **Loop** | **`wisp/runtime/` — step, budget, failure taxonomy** |
| Tools   | `wisp/tools/` — contract + dispatch + built-ins |
| Context | `wisp/core/context_trust.py` + the assembler — assembly, truncation, untrusted handling |
| Trace   | `wisp/trace/` — append-only, replayable |
| Eval    | `wisp/eval/` + `wisp/benchmark/` — golden set, scorer, judge seam |
| Config  | `wisp/configs/*.yaml` — the only place capabilities are named |

**The stance this package exists to hold:** the core is generic and every
capability is configuration. There is no `if config == "email"` in the core,
because the core does not know what an email is. `bounds.py` is the first
resident of this layer, and it is the one that makes the stance *mechanical*: a
run cannot start without declaring what it is allowed to consume.
"""
