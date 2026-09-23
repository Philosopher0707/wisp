"""The context trust boundary (migration P8).

Establishes a trust boundary and makes context construction deterministic and
explainable.

**The gap this closes.** Repository text enters the prompt as plain system
sections (`## Codebase Map`, `## Project guidelines`, `## Cross-Session Memory`)
or as `role:"tool"` messages — with **no delimiters, no escaping, no provenance
tags**. The only existing defenses are *prose and authorization*: grounding
prose, a skill guardrail footer, an "UNTRUSTED WEB DATA" note in an agent role,
an eval scenario, and boot guidelines injected with truncation only.

Prose is not a control. A model that has been told "treat this as untrusted" can
still be persuaded otherwise; a **label** cannot be persuaded.

**Why this must exist before P5's authority lands.** A graph that reads
repository content to decide *what to do next* is directly exposed — prompt
injection in a README becomes a plan proposal. Today the blast radius is bounded
by the 19-gate tool pipeline, but the target architecture grants the model
authority to propose `NodeCreate`, `GraphExpand` and `DelegationRequest`. The
boundary must exist **before** that authority is granted, not after.

**Why tags rather than sanitization.** Sanitizing arbitrary repository text is
not solvable — there is no reliable injection detector. Labelling *is* solvable,
and it makes the boundary **auditable**: any policy-relevant decision citing a
`REPOSITORY` item is a defect that can be detected mechanically.

**Staged, like P3.** The plan says to *"start with tagging-only (no behavioural
change), then enforce T1–T4 behind a flag"*. `ContextAssembler`-style callers can
tag items without `WISP_CONTEXT_TRUST`; the rules are enforced when it is on.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Iterable


class TrustTag(StrEnum):
    """Where a context item came from, and therefore what it may influence."""

    SYSTEM = "system"            # built-in rules, architecture invariants
    OPERATOR = "operator"        # .wisp/rules.md, operator criteria, approvals
    REPOSITORY = "repository"    # file contents, repo map, code index, git context
    TOOL_OUTPUT = "tool_output"  # tool results
    EXTERNAL = "external"        # web fetch, MCP results, third-party artifacts


class Influence(StrEnum):
    """What a decision is allowed to rest on."""

    INSTRUCTION = "instruction"  # appears in instruction position
    PLANNING = "planning"        # planning and action selection
    POLICY = "policy"            # policy, authorization, criteria, the tool set


#: T3's table. Trusted sources may influence anything; untrusted sources may
#: inform planning but **never** policy. `EXTERNAL` is called out separately in
#: the architecture document ("never policy") and is the strictest of the three
#: untrusted tags in intent even though the table is the same shape — the
#: distinction is preserved here so a future narrowing has an obvious place.
MAY_INFLUENCE: dict[TrustTag, frozenset[Influence]] = {
    TrustTag.SYSTEM: frozenset(Influence),
    TrustTag.OPERATOR: frozenset(Influence),
    TrustTag.REPOSITORY: frozenset({Influence.PLANNING}),
    TrustTag.TOOL_OUTPUT: frozenset({Influence.PLANNING}),
    TrustTag.EXTERNAL: frozenset({Influence.PLANNING}),
}

#: T1's set: only these may appear in instruction position.
TRUSTED_TAGS = frozenset({TrustTag.SYSTEM, TrustTag.OPERATOR})


def is_trusted(tag: TrustTag | str) -> bool:
    return TrustTag(tag) in TRUSTED_TAGS


def may_influence(tag: TrustTag | str, influence: Influence | str) -> bool:
    """The single authority for T3. Every caller asks this rather than
    re-deriving the table — a second copy is how a boundary rots."""
    return Influence(influence) in MAY_INFLUENCE[TrustTag(tag)]


class TrustViolation(RuntimeError):
    """Raised when a caller tries to place an untrusted item where it may not go.

    Loud by design: a boundary that silently reorders items is a boundary whose
    violations are invisible, and an invisible violation is indistinguishable
    from compliance.
    """


# ── Items ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Provenance:
    """T4 — which file, which hash, which observation.

    Required, not optional. An item that cannot say where it came from cannot be
    trust-tagged with any confidence, and an untraceable item in the prompt is
    exactly the state P8 exists to end.
    """

    source: str
    content_hash: str = ""
    observation: str = ""

    @classmethod
    def of(cls, source: str, content: str,
           observation: str = "") -> "Provenance":
        return cls(source=source, content_hash=_digest(content),
                   observation=observation)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "content_hash": self.content_hash,
                "observation": self.observation}


@dataclass(frozen=True)
class ContextItem:
    """One piece of context, with its tag and its provenance."""

    item_id: str
    tag: TrustTag
    content: str
    provenance: Provenance
    priority: int = 1        # lower is kept longer; 0 is never dropped

    def __post_init__(self) -> None:
        if not isinstance(self.tag, TrustTag):
            object.__setattr__(self, "tag", TrustTag(self.tag))

    @property
    def trusted(self) -> bool:
        return self.tag in TRUSTED_TAGS

    def render(self) -> str:
        """T2 — untrusted content is ALWAYS delimited and labelled.

        Trusted content is emitted as-is: wrapping the system prompt's own rules
        in a "do not follow this" frame would be incoherent. Untrusted content
        gets a fence naming its source, so the model can see where the boundary
        is rather than being asked to remember it.
        """
        if self.trusted:
            return self.content
        return (
            f"<<UNTRUSTED:{self.tag.value.upper()} "
            f"source={self.provenance.source!r}>>\n"
            f"{self.content}\n"
            f"<<END UNTRUSTED:{self.tag.value.upper()}>>"
        )

    def to_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "tag": self.tag.value,
                "priority": self.priority, "trusted": self.trusted,
                "provenance": self.provenance.to_dict(),
                "length": len(self.content)}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


# ── Assembly ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContextRequest:
    """What to assemble. Identical requests must yield identical contexts."""

    items: tuple[ContextItem, ...] = ()
    max_tokens: int = 1200
    #: Stable ordering key. Defaults to the item id so assembly does not depend
    #: on dict or set iteration order — determinism is a requirement (D1–D4),
    #: not an accident.
    order_key: str = "item_id"


@dataclass(frozen=True)
class DroppedItem:
    """A truncation or omission, recorded as STRUCTURED data (P8 item 2).

    `context_assembler._fit_sections` already records truncation — but as
    **prose inside the prompt** (`[NOTE: Some sections were truncated or
    omitted ...]`). The same distinction as P2's `controlling_layer`: recorded
    as prose is not recorded as *data*, and prose cannot be asserted on, counted
    or alerted on.
    """

    item_id: str
    tag: TrustTag
    reason: str
    tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "tag": self.tag.value,
                "reason": self.reason, "tokens": self.tokens}


@dataclass(frozen=True)
class Context:
    """The assembled result: the prompt, and what was left out of it."""

    system: str
    items: tuple[ContextItem, ...] = ()
    dropped: tuple[DroppedItem, ...] = ()
    tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"tokens": self.tokens,
                "items": [i.to_dict() for i in self.items],
                "dropped": [d.to_dict() for d in self.dropped],
                "trusted_items": sum(1 for i in self.items if i.trusted),
                "untrusted_items": sum(1 for i in self.items if not i.trusted)}


def _estimate_tokens(text: str) -> int:
    """Cheap, deterministic estimate.

    Deliberately not `tiktoken`: it is not installed (`.venv` is missing it), and
    a token count that changes with an optional dependency would break the
    determinism requirement. Chars/4 is stable everywhere.
    """
    return max(1, len(text) // 4)


def assemble(request: ContextRequest,
             *, enforce: bool = False) -> Context:
    """Build a `Context` deterministically.

    Determinism (D1–D4): the same `ContextRequest` always yields the same
    `Context`. Items are sorted by `(priority, order_key)` — never by insertion
    order, never by set iteration order.

    `enforce=False` is **tagging-only** (the plan's stage 1): items are tagged
    and rendered with their delimiters, and nothing is refused. `enforce=True`
    applies T1 — an untrusted item placed at priority 0, i.e. in instruction
    position, raises `TrustViolation`.
    """
    items = tuple(sorted(request.items,
                         key=lambda i: (i.priority, getattr(i, request.order_key))))

    if enforce:
        for item in items:
            # T1 — instruction position is priority 0 (never dropped), which is
            # the only position this contract can treat as authoritative.
            if item.priority == 0 and not item.trusted:
                raise TrustViolation(
                    f"{item.item_id!r} is {item.tag.value} and may not appear in "
                    "instruction position (T1)")

    included: list[ContextItem] = []
    dropped: list[DroppedItem] = []
    parts: list[str] = []
    used = 0

    for item in items:
        rendered = item.render()
        size = _estimate_tokens(rendered)
        if used + size <= request.max_tokens:
            included.append(item)
            parts.append(rendered)
            used += size
            continue
        if item.priority == 0:
            # Priority 0 is never dropped: the plan's own assembler keeps a
            # truncated memory block rather than losing the operator's
            # remembered preferences, and the same rule applies here. Recorded
            # as truncated, not dropped.
            remaining = max(0, request.max_tokens - used)
            if remaining <= 0:
                dropped.append(DroppedItem(item.item_id, item.tag,
                                           "no budget left", size))
                continue
            cut = rendered[:remaining * 4]
            included.append(replace(item, content=cut))
            parts.append(cut)
            used += _estimate_tokens(cut)
            dropped.append(DroppedItem(item.item_id, item.tag, "truncated", size))
            continue
        dropped.append(DroppedItem(item.item_id, item.tag,
                                   "over budget", size))

    system = "\n\n".join(parts)
    return Context(system=system, items=tuple(included),
                   dropped=tuple(dropped), tokens=_estimate_tokens(system))


def assert_may_influence(items: Iterable[ContextItem],
                         influence: Influence) -> list[str]:
    """T3 — return the ids of items that may NOT influence `influence`.

    Returns rather than raises: this is an *audit*, and its callers include
    tests and diagnostics that want the whole list, not the first violation.
    An empty list means the boundary held.
    """
    return [i.item_id for i in items if not may_influence(i.tag, influence)]
