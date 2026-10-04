# Checklist — concurrency and isolation

Run when the change introduces state, a bound, or a callback that a different
execution context can reach.

## The state

- [ ] Every piece of new state is classified: per call / per turn / per run / per process / global.
- [ ] Nothing is **global** without an explicit justification in the decision record.
- [ ] Per-turn or per-run state is **constructed where the unit begins**, not cached above it.
- [ ] Nothing is cached across units that should be per-unit.

## Isolation

- [ ] Two concurrent instances of the same path cannot see each other's state.
- [ ] Two sequential units do not share a counter (a fresh budget per unit).
- [ ] Interleaved execution of two units produces the same results as sequential execution.
- [ ] A callback handed to another component captures **its own unit's** state, not a shared one.

## Boundaries and contexts

- [ ] If a context variable / thread-local / async-local is used, its setter, lifetime and reset semantics are named.
- [ ] Nested execution (a unit started inside a unit) receives its **own** state, or explicitly receives none.
- [ ] Cancellation of a unit discards its state and cannot affect another unit.
- [ ] No state survives the unit's lifetime.

## Paths that bypass the new wiring

- [ ] Every entry point that reaches the changed site is enumerated.
- [ ] Entry points that do **not** pass the new input are listed, and their behaviour is stated (usually: the new behaviour does not apply).
- [ ] That absence is **consistent and documented**, not accidental.
- [ ] No effort was made to propagate the new state to those paths unless the decision requires it.

## Ordering

- [ ] When two gates sit at the same site, the order is asserted (not assumed).
- [ ] The earlier gate's `continue`/short-circuit means the later one cannot double-act.
- [ ] Both counters are monotone, so the loop provably terminates.
- [ ] No ordering depends on dictionary iteration or set iteration order.

## Evidence

- [ ] A test drives two instances concurrently and asserts independent state.
- [ ] A test drives two sequential units and asserts a fresh budget.
- [ ] Locality is proven by AST where the claim is "this is a local, not an attribute".
