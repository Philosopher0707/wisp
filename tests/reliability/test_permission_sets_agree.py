"""The three copies of each permission set must agree — committed, not only in one working tree.

`core/contracts.py:266` names the hazard itself: *"current drift source: three hand-maintained
frozensets in `infra/security.py:38-56`"*. `capability_filter.py:20` says it from the other side:
*"equality with the enforcement set is pinned by test. If a 15th safe tool lands, update this set AND
the pin together."*

**That pin is `tests/reliability/test_13i2_capability_filter.py`, and it is NOT TRACKED** — it is a
working-tree file. So on a fresh clone the sentence above is false: three hand-maintained copies of the
same authority, with nothing checking that they agree, on two different live paths:

  - `infra/security.py`'s sets  → `SecurityPolicy.__post_init__` passes them into the engine's rules.
    This is the ENFORCEMENT path.
  - `infra/policy_engine.py`'s `_DEFAULT_*` → the engine's fallbacks AND `filter_allowed_for_mode`,
    which is what a SUBAGENT is told it may call.
  - `capability_filter.py`'s `READ_ONLY_TOOLS` → the subagent capability filter.

A tool added to one and not the others makes the agent's *advertised* surface disagree with what is
*enforced* — either a child is told it may call something the gate then denies, or it is never told
about something it may.

Measured 2026-09-27: all three agree (14 / 13 / 7). This keeps them that way **in the repository**.
"""
from __future__ import annotations

from wisp import capability_filter as C
from wisp.infra import policy_engine as P
from wisp.infra import security as S

#: A floor. Three empty sets would satisfy every equality below.
SET_FLOOR = 5


def test_safe_read_set_agrees_across_all_three_copies():
    copies = {
        "infra/security.py::_SAFE_READ_TOOLS": S._SAFE_READ_TOOLS,
        "infra/policy_engine.py::_DEFAULT_SAFE_READ_TOOLS": P._DEFAULT_SAFE_READ_TOOLS,
        "capability_filter.py::READ_ONLY_TOOLS": C.READ_ONLY_TOOLS,
    }
    assert all(len(v) >= SET_FLOOR for v in copies.values()), f"a copy is empty: {copies}"
    ref_name, ref = next(iter(copies.items()))
    for name, s in copies.items():
        assert s == ref, (
            f"{name} disagrees with {ref_name}. The three copies of the safe-read set have drifted; "
            f"only in {name}: {sorted(s - ref)}; missing from {name}: {sorted(ref - s)}")


def test_auto_edit_block_set_agrees():
    a = S._AUTO_EDIT_BLOCK_TOOLS
    b = P._DEFAULT_AUTO_EDIT_BLOCK
    c = P._AUTO_EDIT_DENY_TOOLS | P._AUTO_EDIT_APPROVAL_TOOLS
    assert len(a) >= SET_FLOOR
    assert a == b == c, (
        f"the AUTO_EDIT block set has drifted — security.py={sorted(a)}, "
        f"policy_engine._DEFAULT={sorted(b)}, DENY|APPROVAL={sorted(c)}")


def test_ask_all_block_set_agrees():
    a = S._ASK_ALL_BLOCK_TOOLS
    b = P._DEFAULT_ASK_ALL_BLOCK
    assert len(a) >= SET_FLOOR
    assert a == b, (
        f"the ASK_ALL approval set has drifted — only in security.py: {sorted(a - b)}; "
        f"only in policy_engine: {sorted(b - a)}")


#: Which copy is ENFORCED is a question this file deliberately does not test.
#:
#: Two attempts were made and both were withdrawn. The first probed `repr(engine.__dict__)` — the
#: brittle-string-matching pattern this repository spent a session removing, and the engine stores its
#: sets inside rule closures that no `repr` shows. The second called `SecurityPolicy.check_allow`, which
#: does not exist; the API is `check(action, context)`.
#:
#: The answer is a READ, not a probe, and it is recorded here so nobody re-derives it:
#: `SecurityPolicy.__post_init__` passes `security.py`'s three frozensets into
#: `PriorityRuleEngine.with_builtin_rules(...)` explicitly, so the engine's `_DEFAULT_*` are
#: **overridden** on that path. `policy_engine`'s defaults remain live only for
#: `filter_allowed_for_mode`, which is what a subagent is TOLD it may call.
#:
#: Shipping three tests that hold beats shipping a fourth built on a guess about an API.
