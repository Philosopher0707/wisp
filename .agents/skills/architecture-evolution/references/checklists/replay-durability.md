# Checklist — replay and durability

Only relevant when the change creates or alters a **durable decision**. If it
does not, say so and skip — but check that first.

## The separation

- [ ] The **durable decision inputs** are listed explicitly.
- [ ] The **transient execution state** is listed explicitly.
- [ ] No transient state is persisted as if it were an input.
- [ ] No decision input is left unrecorded.

## The invariant

```text
reconstruct(recorded inputs) == live decision made from the same inputs
```

- [ ] The decision is a **pure function** of the recorded inputs (no clock, no random, no live component, no model call).
- [ ] The derivation is **total** — every input combination produces a state, none falls through.
- [ ] A test drives **all** input combinations and asserts the image is exactly the intended set of states.

## Reconstruction

- [ ] Reconstructing from the record alone reproduces the live decision.
- [ ] Reconstruction after a **restart** (new process, same store) reproduces it.
- [ ] Reconstruction **without** the live component (which no longer exists) reproduces it.
- [ ] A missing or unreadable authoritative input **fails loud** rather than defaulting.
- [ ] "Missing" and "unreadable" are distinguished, not collapsed.

## Terminal states

- [ ] A terminal state is **frozen**: a later observation cannot rewrite it.
- [ ] A duplicate record cannot rewrite it.
- [ ] Replay cannot derive a different terminal state from the same journal.

## Optionality

- [ ] Every input that is optional today is identified.
- [ ] For each, it is stated whether it becomes **mandatory** when the decision is authoritative.
- [ ] A defaulted input cannot be mistaken for a real one in the record.
- [ ] The record can explain its own conclusion — a reader with only the record can see **why** the state was derived.

## Duplicate producers

- [ ] The record's fields are written from **one** computation, not two.
- [ ] Where two fields describe the same fact, they are derived from the same source (or the divergence is intended and documented).
- [ ] No field is recomputed by a second call that could drift from the first.
