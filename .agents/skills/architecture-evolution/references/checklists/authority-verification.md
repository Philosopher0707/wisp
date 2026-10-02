# Checklist — authority verification

Run after implementing, and again before declaring the phase complete. The point
is to prove ownership, not to assert it.

## One owner per rung

- [ ] For each rung the change touches, the owner is named in source (`file:line`).
- [ ] No rung has **two** owners. Where two exist, they are consolidated or the delta is pinned as a deliberate decision.
- [ ] The new component's **imports** are enumerated, and they contain no authority it does not own.
- [ ] The new component **cannot** mutate the existing owner (no owner object crosses).

## The existing owner still owns it

- [ ] The pre-existing gate/guard/verdict is consulted **before** the new one, in source order.
- [ ] Its behaviour is unchanged: the file is **zero-diff**, or the delta is stated and pinned.
- [ ] Its own bound still surrenders honestly.
- [ ] The new path cannot bypass it (assert the ordering, not the intention).

## Producer uniqueness

- [ ] Exactly **one** module produces the decision. Asserted by an AST ratchet over assignment targets, not a text search.
- [ ] If a reader is permitted, the ratchet distinguishes reading from producing.
- [ ] The ratchet is **non-vacuous** — it fails if the pattern disappears entirely.

## Consumer honesty

- [ ] Every mechanism the phase relies on has a **production consumer** (not a test, not a docstring).
- [ ] Anything still unconsumed is **stated as unconsumed**, with a tripwire.
- [ ] No claim of "wired" is made on the basis of a definition, a type, a flag or a test.

## State and persistence

- [ ] New state is a **local**, proven by AST (a store to a name, not to an attribute).
- [ ] Nothing global was introduced; if it was, the justification is in the decision record.
- [ ] What is persisted is exactly the **decision inputs**, no more.
- [ ] Nothing transient is persisted as if it were an input.

## Configuration

- [ ] The flag's default is **off** (unless the decision says otherwise, in writing).
- [ ] The flag is read at one site.
- [ ] With the flag off, the default path is **byte-for-byte** the previous call.
- [ ] Disabling the upstream producer also disables this consumer (no orphan activation).

## Evidence

- [ ] Each claim above cites a **command, a test or a diff** — not an assurance.
- [ ] Claims that could only be read are marked **unverified**.
