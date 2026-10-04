# Checklist — adversarial testing

The goal is not coverage. It is to attack the specific ways this change could
become a second authority, a false success, or an unbounded loop.

## False success

- [ ] A success signal arrives while the real condition is unmet — does the outcome stay honest?
- [ ] A terminal "done"/"success" event arrives after an authoritative failure — does the failure stand?
- [ ] Inconclusive evidence present — does it resolve to anything other than a pass?
- [ ] Unknown / not-yet-evaluated input — does it stay non-blocking **without** becoming a pass?
- [ ] A progress signal that later recovers — is the final state derived from the **current** input, or from a latch? (If a latch, say so: it changes what the change can achieve.)

## Second authority

- [ ] Drive the new component and the existing owner with the same input; do they ever disagree?
- [ ] Can the new component's state be mutated by anyone other than its owner?
- [ ] Is there any path where the new decision is made without the owner being consulted?
- [ ] Does any *record* now contradict its own conclusion (e.g. a field says one thing, the derived state says another)?
- [ ] Does reconstructing from the record reproduce the live decision in **every** case, including the awkward one?

## Failure erasure

- [ ] Fatal error with the new condition also active — which wins?
- [ ] Cancellation with the new condition active — is cancellation preserved?
- [ ] Timeout / budget exhaustion with the new condition active — is the reason still recorded?
- [ ] Duplicate terminal record — is the first one frozen?

## Bounds and loops

- [ ] Exactly at the limit, one past it, and one remaining — all three behave.
- [ ] A permanently unsatisfiable condition still terminates.
- [ ] The bound reached at another limit's edge does not become a harder failure.
- [ ] Two counters cannot advance each other; the loop provably terminates.

## Flags and defaults

- [ ] Flag OFF: the new path is unreachable, and the old path is unchanged.
- [ ] Flag ON: the new path is reachable.
- [ ] Producer disabled: the consumer goes quiet rather than misbehaving.
- [ ] Two independent flags do not silently interact.

## Isolation

- [ ] Two concurrent instances cannot see each other's state.
- [ ] A second run gets a fresh budget (nothing leaked from the first).
- [ ] Paths that bypass the new wiring (other entry points, sub-workers) are **stated**, not discovered later.

## The ratchets themselves

- [ ] Every ratchet is **AST-based**, not a whole-file text search (a text search matches the comment describing the problem, including your own).
- [ ] Every ratchet fails when the property is removed (test it by breaking it deliberately).
- [ ] No existing ratchet was weakened to make the change pass.
