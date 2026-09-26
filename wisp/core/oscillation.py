"""Oscillation detection: a stable diff identity, and a 1-cycle/2-cycle trap.

**Why this module exists (ADR-0060 R5, Layer C's disposition).** Both symbols
here used to live in `wisp/core/graph/loop.py` — Layer C, which ADR-0001 named
*"disowned"*. But they were never dead: `core/stagnation.py` (wired to the live
turn path by M13/ADR-0034) reuses `OscillationTrap`, and `core/runtime.py` uses
`diff_hash`. So the live path depended on a layer the corpus had declared
disowned — the *disowned-but-consumed* shape this repository keeps finding.

Relocating them here makes that claim true: **Layer A no longer imports Layer C
at all**, and `wisp/core/graph/` is left with zero live consumers. The move is
mechanical — the bodies are unchanged — and `core/graph/loop.py` re-exports both
names, so nothing that imported them from there breaks.

**The dependency direction is the point.** A disowned layer may import from a
live one (`loop.py` imports this module); the live path may not import from a
disowned one. `tests/reliability/test_layer_c_disposition.py` asserts exactly
that direction and nothing else.

`OscillationTrap` is monotonic: `trap_fired` never clears inside a turn
(ADR-0037). That is a property of the trap, not of the module it lived in, so
the relocation cannot change it — and the guard drives it rather than asserting
that it does.
"""

from __future__ import annotations

import hashlib


def diff_hash(diff: str) -> str:
    """Stable identity of a produced diff for oscillation detection."""
    return hashlib.sha256(diff.encode("utf-8", errors="ignore")).hexdigest()


class OscillationTrap:
    """Detects 1-cycle repeats and 2-cycle oscillations of diff hashes."""

    def __init__(self) -> None:
        self._hashes: list[str] = []

    def observe(self, digest: str) -> str | None:
        """Record a diff hash; return 'repeat' | 'cycle' | None."""
        self._hashes.append(digest)
        if len(self._hashes) >= 2 and self._hashes[-1] == self._hashes[-2]:
            return "repeat"
        if len(self._hashes) >= 3 and self._hashes[-1] == self._hashes[-3]:
            return "cycle"
        return None
