"""The system prompt and the task are never dropped — they raise instead.

The spec: *"System prompt + task are never dropped. If they don't fit →
`context_overflow` error. Not 'silently send something else.' This prevents the
subtle bug where truncation quietly removes the task and the agent hallucinates a
different goal."*

**What this replaces.** `assemble()` treated `priority == 0` as "never dropped",
but the branch that implemented it did this:

    remaining = max(0, request.max_tokens - used)
    cut = rendered[:remaining * 4]          # ← silently sends a prefix
    ...
    dropped.append(DroppedItem(..., "truncated", size))

So a system prompt or a task that did not fit was **truncated** — replaced by its
own first paragraph — and the run continued on a prompt nobody wrote. That is
worse than dropping it outright: a missing task is a prompt the model will fill in
from whatever remains, and the turn still reports success.

`priority == 0` is not the same property. It is also used for an operator's
remembered preferences, where keeping a *truncated* block is deliberate and
correct. So protection is its own thing: a tag (`PROTECTED_TAGS`) for the system
prompt, and an explicit field for the task, which has no tag of its own.
"""
from __future__ import annotations

import pytest

from wisp.core.context_trust import (
    PROTECTED_TAGS,
    ContextItem,
    ContextOverflow,
    ContextRequest,
    Provenance,
    TrustTag,
    assemble,
)

TASK = "Refactor the retry policy in wisp/core/provider_stream.py."


def _item(item_id: str, content: str, *, tag=TrustTag.REPOSITORY, priority=1,
          protected=False) -> ContextItem:
    return ContextItem(
        item_id=item_id, tag=tag, content=content,
        provenance=Provenance.of(f"{item_id}.txt", content),
        priority=priority, protected=protected,
    )


def _system(content: str) -> ContextItem:
    return _item("system", content, tag=TrustTag.SYSTEM, priority=0)


def _task(content: str = TASK) -> ContextItem:
    # The task carries OPERATOR (it is the user's instruction) but is protected
    # explicitly, because OPERATOR also covers rules.md and criteria, which are
    # legitimately droppable.
    return _item("task", content, tag=TrustTag.OPERATOR, priority=0, protected=True)


def _request(items, max_tokens: int) -> ContextRequest:
    return ContextRequest(items=tuple(items), max_tokens=max_tokens)


# ── The property ────────────────────────────────────────────────────────


class TestProtectedItemsAreNeverShortened:
    def test_a_protected_item_that_does_not_fit_raises(self):
        """**The forcing case.** Before this, the task was truncated and the run
        continued on a prompt nobody wrote."""
        req = _request([_system("s" * 40), _task("t" * 4000)], max_tokens=60)
        with pytest.raises(ContextOverflow):
            assemble(req)

    def test_the_error_carries_the_machine_readable_code(self):
        req = _request([_system("s" * 4000)], max_tokens=10)
        with pytest.raises(ContextOverflow) as excinfo:
            assemble(req)
        assert excinfo.value.code == "context_overflow", (
            "a caller must branch on `code`, not on the message")

    def test_the_system_prompt_is_protected_by_its_tag(self):
        """No caller has to remember: a SYSTEM item is protected because of what
        it is."""
        assert TrustTag.SYSTEM in PROTECTED_TAGS
        req = _request([_system("s" * 4000)], max_tokens=10)
        with pytest.raises(ContextOverflow):
            assemble(req)

    def test_the_task_is_protected_by_its_field(self):
        """The task has no tag of its own, so it is marked explicitly."""
        req = _request([_task("t" * 4000)], max_tokens=10)
        with pytest.raises(ContextOverflow):
            assemble(req)

    def test_a_protected_item_that_fits_is_included_in_full(self):
        """The rule must not over-apply: protection is not 'refuse everything'."""
        task = _task()
        out = assemble(_request([_system("rules"), task], max_tokens=10_000))
        assert task.content in out.system, "the task was not included verbatim"
        assert not out.dropped, "a fitting protected item was recorded as dropped"

    def test_the_task_survives_extreme_pressure_from_other_items(self):
        """The scenario the spec names: a huge context squeezes everything else,
        and the task is the one thing that must come through — or the run fails."""
        filler = [_item(f"f{i}", "x" * 4000) for i in range(20)]
        out = assemble(_request([_system("rules"), _task(), *filler],
                                max_tokens=10_000))
        assert TASK in out.system, "the task was lost under pressure"
        assert len(out.dropped) >= 10, "the filler was not dropped, so this test "
        # did not actually apply pressure

    def test_the_error_names_the_item_and_the_budget(self):
        req = _request([_system("s" * 4000)], max_tokens=10)
        with pytest.raises(ContextOverflow) as excinfo:
            assemble(req)
        text = str(excinfo.value)
        assert "system" in text, "the item is not named"
        assert "10" in text, "the budget is not named"


# ── The design that must survive ────────────────────────────────────────


class TestUnprotectedItemsKeepTheirBehaviour:
    """Protection is a new property, not a redefinition of `priority == 0`.

    The memory-block truncation is deliberate: losing the operator's remembered
    preferences entirely is worse than keeping a shortened block. Removing that
    behaviour would be a regression dressed as a fix.
    """

    def test_an_unprotected_priority_0_item_is_still_truncated(self):
        """Isolated to the one item, deliberately.

        With a system prompt also present this raises — and correctly: the
        truncation consumes *all* remaining budget (`remaining = max_tokens -
        used`), so nothing else fits afterwards. Testing the two together would
        be testing the interaction, not the property.
        """
        mem = _item("memory", "m" * 4000, tag=TrustTag.OPERATOR, priority=0)
        out = assemble(_request([mem], max_tokens=40))
        assert any(d.item_id == "memory" and d.reason == "truncated"
                   for d in out.dropped), (
            "the memory block was not truncated — the deliberate priority-0 "
            "behaviour was removed rather than left alone")
        assert len(out.system) < len(mem.content), "the block was not shortened"

    def test_an_unprotected_priority_1_item_is_still_dropped(self):
        big = _item("big", "b" * 4000, priority=1)
        out = assemble(_request([_system("rules"), big], max_tokens=40))
        assert any(d.item_id == "big" for d in out.dropped)

    def test_an_unprotected_item_never_raises(self):
        """Only protection raises. A droppable item must not be able to fail a
        turn — otherwise every large file would become an outage."""
        req = _request([_system("rules"),
                        *[_item(f"f{i}", "x" * 4000) for i in range(20)]],
                       max_tokens=40)
        out = assemble(req)          # must not raise
        assert out.dropped

    def test_the_protection_floor_is_not_vacuous(self):
        """A floor: if `PROTECTED_TAGS` were empty AND no item were ever marked
        protected, every raise-test above would still pass by constructing its own
        item — so assert the two mechanisms both have a member."""
        assert PROTECTED_TAGS, "no tag is protected, so the tag mechanism is dead"
        assert _task().protected, "the explicit mechanism has no member here"
