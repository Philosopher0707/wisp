"""Which paths a shell command would write, from the invariant gates' own parser. Pure.

A file edited through `sed -i`, `tee` or a redirect is a mutation just as much as `edit_file`: without this, a fix made in the shell would
leave an earlier passing run looking fresh. The answer is conservative: a command that cannot be parsed names no paths (the gate, not this
module, refuses it)."""

from __future__ import annotations

from wisp.core.gates.invocations import extract
from wisp.core.gates.paths import write_targets
from wisp.core.gates.shellparse import parse


def shell_write_paths(command: str) -> tuple[str, ...]:
    try:
        parsed = parse(command)
        if not parsed.ok or parsed.tree is None:
            return ()
        invocations, _ = extract(parsed.tree, None, "")
        out: list[str] = []
        for inv in invocations:
            out += [t.word.text for t in write_targets(inv) if t.word.text and t.word.text != "/dev/null"]
        return tuple(dict.fromkeys(out))
    except Exception:  # noqa: BLE001 — an analysis failure means "no known writes", never an error in the turn
        return ()
