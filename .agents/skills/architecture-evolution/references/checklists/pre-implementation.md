# Checklist — before implementing anything

Run this after recon and before the first production edit. A `no` is a stop
condition, not a note.

## The change is bounded

- [ ] The contract or decision record exists and is **ratified**.
- [ ] The seam is named, and it is an **existing boundary** (or the new boundary is justified).
- [ ] The files to change are listed, and **nothing else** is in scope.
- [ ] The non-goals list exists and is copied into the implementation plan.

## The authority is clear

- [ ] The rung being changed has **exactly one owner**, named.
- [ ] That owner is **not** being duplicated by this change.
- [ ] What the new component may decide is written down.
- [ ] What it **cannot** decide is written down.
- [ ] Nothing acquires authority by accident (model output, user input, plugin, tool).

## The state is safe

- [ ] New state is **local** (per call / per turn / per run), or a global is explicitly justified.
- [ ] The owner object itself does **not** cross the boundary — only a read-only value or predicate.
- [ ] Nothing new is persisted that is not a **decision input**.

## The behaviour is reversible

- [ ] A flag exists, and **OFF reproduces previous behaviour exactly**.
- [ ] The flag is read at **one site**.
- [ ] Rollback levels are named, with their defaults.

## The behaviour is bounded

- [ ] Every retry / replan / recovery / recursion / poll / fanout has a **maximum**.
- [ ] The owner of each bound is named, and when it resets.
- [ ] Exhaustion produces an **honest surrender**, not a harder failure.
- [ ] The interaction with adjacent limits is checked.

## The interface is compatible

- [ ] Every **call site** of a changed interface is accounted for.
- [ ] Every **implementation** of a changed interface is accounted for (test doubles, adapters, other backends).
- [ ] A new optional argument is passed **only when needed**, so the default path emits the old call.
- [ ] Anything that adds a record to every caller's log is **gated**.

## The baseline exists

- [ ] The tree was snapshotted **outside** the repository.
- [ ] No `git stash` / `reset --hard` / `checkout --` was used.
- [ ] Pre-existing uncommitted work is identified and treated as immutable.
- [ ] The stable failure set exists (intersection of two runs), or its absence is stated.
