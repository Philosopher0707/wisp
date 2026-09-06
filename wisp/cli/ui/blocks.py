"""Viewport data model: blocks, collapse set, viewport, telemetry, pending gate.

Formatting lives in wisp/transport/renderer.py; this module holds data only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import time


@dataclass
class Block:
    id: str
    kind: str  # plan | thought | tool | diff | log | gate
    payload: dict
    ts: float = field(default_factory=time.monotonic)


@dataclass
class ScreenModel:
    cap: int = 500
    blocks: list = field(default_factory=list)
    collapsed: set = field(default_factory=set)
    viewport: str = "scrollback"  # lowercase "scrollback" | "altscreen" (spec shouting is prose emphasis)
    telemetry: dict = field(default_factory=dict)
    pending_gate: dict | None = None
    _seq: int = 0

    def append(self, kind: str, payload: dict) -> Block:
        b = Block(id=f"blk-{self._seq}", kind=kind, payload=payload)
        self._seq += 1
        if kind == "thought":
            self.collapsed.add(b.id)
        self.blocks.append(b)
        for dropped in self.blocks[:-self.cap]:
            self.collapsed.discard(dropped.id)
        del self.blocks[:-self.cap]
        return b

    def is_collapsed(self, block_id: str) -> bool:
        return block_id in self.collapsed

    def toggle(self, block_id: str) -> None:
        if block_id in self.collapsed:
            self.collapsed.discard(block_id)
        else:
            self.collapsed.add(block_id)

    def toggle_newest_collapsible(self):
        for b in reversed(self.blocks):
            if b.kind in ("thought", "diff"):
                self.toggle(b.id)
                return b
        return None


#: Event kinds painted into scrollback by CLIEventRenderer. Thought/tool rows
#: are owned by the transport streaming path today (pager/alt-screen consumers
#: use their renderers next); painting them here too would duplicate output.
STREAM_PAINT_KINDS = frozenset({"plan", "gate", "log", "diff"})


def reduce_event(model: ScreenModel, event: dict) -> list:
    """Map one agent event to viewport block(s). Returns affected blocks.

    Contract evolution (wiring follow-up): chunk 1 returned [] always;
    painters need the affected blocks, so appended-or-updated Blocks are
    returned. Key/Resize/Tick/SigInt stay runner-owned.
    """
    affected = []
    etype = (event or {}).get("type", "")
    data = (event or {}).get("data", {}) or {}
    if etype == "thinking" and str(data.get("text", "")).strip():
        affected.append(model.append("thought", {"text": data["text"]}))
    elif etype == "tool_call":
        affected.append(model.append("tool", {"name": data.get("name", "?"),
                              "arguments": data.get("arguments", {}),
                              "status": "running"}))
    elif etype == "tool_result":
        for b in reversed(model.blocks):
            if b.kind == "tool" and b.payload.get("status") == "running":
                b.payload.update({"status": "done",
                                  "summary": str(data.get("summary") or data.get("result") or "")[:120]})
                affected.append(b)
                break
    elif etype in ("plan", "diff", "log", "gate"):
        payload = dict(data)
        if etype == "diff":
            pairs = payload.get("files", [])
            if pairs and isinstance(pairs, list) and pairs and isinstance(pairs[0], (list, tuple)) and len(pairs[0]) == 3 and isinstance(pairs[0][1], str):
                from wisp.cli.ui.pager import summarize_diffs
                payload["counts"] = summarize_diffs(pairs)
        affected.append(model.append(etype, payload))
    return affected


def diff_pager_effect(model: ScreenModel, key: str):
    """Map the v keystroke to ("open_pager", texts) for the newest pageable diff.

    Returns None when the key is not v or no pageable diff exists.
    The runner consumes the effect by calling pager.show_diff(texts) while
    holding TerminalGuard (same input-loop hook as the Space binding).
    """
    if key != "v":
        return None
    from wisp.cli.ui.pager import should_page_diff
    for b in reversed(model.blocks):
        if b.kind == "diff":
            pairs = b.payload.get("files", [])
            if pairs and should_page_diff(pairs):
                return ("open_pager", pairs)
    return None


def expand_newest(model: ScreenModel):
    """Toggle newest collapsible; when expanding, append full text as a log block.

    Returns the appended log Block, or None (collapse direction, or nothing
    collapsible). Collapse is intentionally silent: scrollback cannot un-print,
    and the earlier collapsed row plus any prior expansion remain as history.
    """
    b = model.toggle_newest_collapsible()
    if b is not None and not model.is_collapsed(b.id):
        text = b.payload.get("text", "")
        return model.append("log", {"text": f"Expanded {b.kind} {b.id}: {text}"})
    return None
