"""Four mandatory bounds, and no defaults. A run that cannot state its ceiling
does not start.

## The invariant

    max_steps  ·  max_tokens_total  ·  max_wall_clock_s  ·  max_cost_usd

**All four are required. None has a default.** A configuration missing any one of
them **fails to load** — it does not silently inherit a generous value, and it does
not fall back to "unbounded". This eliminates an entire incident class rather than
mitigating it: *"the runaway agent burned $4,000 overnight"* is not a bug to be
detected, it is a configuration that was never allowed to exist.

The reason there are no defaults is that **every default is a policy nobody
chose**. Pick a small default and a legitimate long run dies at step 40; pick a
large one and the ceiling is decorative. The only safe value is the one the
operator wrote down, which is why the loader refuses instead of guessing.

## Why the ranges differ, and why that is not an inconsistency

`max_steps` and `max_tokens_total` are **countable**: zero is a legitimate "start
nothing", and a zero must *terminate* rather than hang. `max_wall_clock_s` and
`max_cost_usd` are **rates of consumption**: zero is not a small budget, it is a
run that can never do anything, so a zero there is a configuration error and is
rejected at load rather than discovered at the first tick.

## Where this sits

`wisp/runtime/` is the **Loop** layer. This module is the layer's budget half; the
step and the failure taxonomy join it here. The bounds are read from
`wisp/configs/*.yaml`, which is the only place capabilities are named — the core
reads a number, never a capability.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, fields
from typing import Any, Mapping

#: The four, in the order the architecture states them. A tuple, so a caller can
#: iterate the requirement rather than re-spelling it — a second spelling is how a
#: bound stops being a bound.
BOUND_NAMES: tuple[str, ...] = (
    "max_steps",
    "max_tokens_total",
    "max_wall_clock_s",
    "max_cost_usd",
)

#: Bounds that count things. Zero is a legitimate "start nothing", and it must
#: terminate rather than hang — so the floor is inclusive.
COUNTABLE: frozenset[str] = frozenset({"max_steps", "max_tokens_total"})

#: Bounds that bound a rate of consumption. Zero is not a small budget; it is a
#: run that can never do anything, so the floor is exclusive.
CONSUMPTION: frozenset[str] = frozenset({"max_wall_clock_s", "max_cost_usd"})


class BoundsError(RuntimeError):
    """A configuration's bounds are missing or invalid, so the run does not start.

    Carries `code` (stable, for branching) and `missing` (the names absent), so a
    caller can report *which* ceiling was not declared instead of parsing prose.
    """

    code = "bounds_invalid"

    def __init__(self, message: str, *, missing: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.missing = missing


@dataclass(frozen=True)
class RunBounds:
    """A run's four ceilings. **Every field is required — there are no defaults.**

    That is not a stylistic choice: a dataclass with defaults *is* a set of
    defaults, and the first `RunBounds()` that compiles becomes the policy for
    every run that forgot to declare one. The absence of a default is what makes
    "you must consciously pick a ceiling" true rather than aspirational.

    Constructing directly validates the ranges; `from_mapping` additionally
    requires that nothing is missing.
    """

    max_steps: int
    max_tokens_total: int
    max_wall_clock_s: float
    max_cost_usd: float

    def __post_init__(self) -> None:
        for name in BOUND_NAMES:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BoundsError(
                    f"{name} must be a number, got {type(value).__name__} "
                    f"({value!r}) — a bound that is not a number is not a bound"
                )
            if name in COUNTABLE and value < 0:
                raise BoundsError(
                    f"{name} must be >= 0, got {value} — zero is a legitimate "
                    "'start nothing', and it must terminate rather than hang"
                )
            if name in CONSUMPTION and value <= 0:
                raise BoundsError(
                    f"{name} must be > 0, got {value} — zero here is not a small "
                    "budget, it is a run that can never do anything"
                )

    def as_dict(self) -> dict[str, float]:
        """The four, by name — so a trace or a report cannot omit one silently."""
        return {name: getattr(self, name) for name in BOUND_NAMES}

    def exhausted(self, *, steps: int, tokens: int, elapsed_s: float,
                  cost_usd: float) -> tuple[str, ...]:
        """Which bounds are already met or passed. Empty means the run may continue.

        Returned as a tuple rather than a bool because **a run can be out of
        budget in more than one way at once**, and collapsing that to a single
        reason loses the one the reader needs. The caller decides precedence; this
        function refuses to.
        """
        usage = {"max_steps": steps, "max_tokens_total": tokens,
                 "max_wall_clock_s": elapsed_s, "max_cost_usd": cost_usd}
        return tuple(n for n in BOUND_NAMES if usage[n] >= getattr(self, n))


def from_mapping(data: Mapping[str, Any], *, source: str = "<mapping>") -> RunBounds:
    """Build `RunBounds` from a mapping, requiring all four.

    `source` names where the mapping came from so the failure says *which file* is
    incomplete — the whole point of the invariant is that the operator fixes the
    config, and they cannot fix a file they cannot identify.
    """
    if not isinstance(data, Mapping):
        raise BoundsError(f"{source}: bounds must be a mapping, got {type(data).__name__}")

    missing = tuple(n for n in BOUND_NAMES if n not in data)
    if missing:
        raise BoundsError(
            f"{source}: missing {len(missing)} of {len(BOUND_NAMES)} mandatory "
            f"bound(s): {', '.join(missing)}. There are no defaults by design — "
            "every default is a policy nobody chose. Declare all four: "
            f"{', '.join(BOUND_NAMES)}.",
            missing=missing,
        )

    extra = tuple(sorted(set(data) - set(BOUND_NAMES)))
    if extra:
        raise BoundsError(
            f"{source}: unknown bound(s) {', '.join(extra)}. The bound set is "
            f"closed: {', '.join(BOUND_NAMES)}. A name this module does not "
            "enforce would be a ceiling that does not hold."
        )

    # Validate the RAW values, before any coercion.
    #
    # This ordering is load-bearing and was wrong in the first version: coercing
    # first meant `max_steps="forty"` raised a bare `ValueError` from `int()` — an
    # error the caller cannot distinguish from a bug — and `max_steps=True` became
    # `1` *before* `__post_init__` could reject it, since `True` is an `int` in
    # Python. Both were found by running the tests, not by reading them.
    for name in BOUND_NAMES:
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BoundsError(
                f"{source}: {name} must be a number, got {type(value).__name__} "
                f"({value!r}). A bound that is not a number is not a bound."
            )
        if name in COUNTABLE and float(value) != int(value):
            raise BoundsError(
                f"{source}: {name} must be a whole number, got {value!r} — a "
                "fractional step or token count is a configuration error, not a "
                "value to round."
            )

    return RunBounds(
        max_steps=int(data["max_steps"]),
        max_tokens_total=int(data["max_tokens_total"]),
        max_wall_clock_s=float(data["max_wall_clock_s"]),
        max_cost_usd=float(data["max_cost_usd"]),
    )


def load(path: str | pathlib.Path) -> RunBounds:
    """Load the bounds from a YAML config.

    Reads `path`'s `bounds:` mapping. A file with no `bounds:` key is missing all
    four and says so — it is not a file that inherits them.

    YAML is the format the Config layer specifies (`configs/*.yaml`). This function
    is the only place the format is known, so moving to another one is a change
    here and nowhere else.
    """
    import yaml

    p = pathlib.Path(path)
    if not p.exists():
        raise BoundsError(f"{p}: no such config file")
    try:
        document = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BoundsError(f"{p}: not valid YAML: {exc}") from exc

    if not isinstance(document, Mapping):
        raise BoundsError(
            f"{p}: expected a mapping at the top level, got "
            f"{type(document).__name__} — a config with no keys declares no bounds"
        )
    return from_mapping(document.get("bounds", {}), source=f"{p} (bounds:)")


def field_names() -> tuple[str, ...]:
    """The declared fields, read from the dataclass rather than re-listed.

    A guard asserts this equals `BOUND_NAMES`: if someone adds a field to
    `RunBounds` without adding it here, the four-bound requirement would silently
    become five-with-one-unchecked.
    """
    return tuple(f.name for f in fields(RunBounds))


__all__ = [
    "BOUND_NAMES", "COUNTABLE", "CONSUMPTION",
    "BoundsError", "RunBounds", "from_mapping", "load", "field_names",
]
