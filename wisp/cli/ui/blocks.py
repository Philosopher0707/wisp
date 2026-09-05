"""Viewport data model: blocks, collapse set, viewport, telemetry, pending gate.

Formatting lives in wisp/transport/renderer.py; this module holds data only.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    id: str
    kind: str  # plan | thought | tool | diff | log | gate
    payload: dict


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


def reduce_event(model: ScreenModel, event: dict) -> list:
    """Map one agent event to viewport block(s). Returns effects (chunk 1: always []).

    Key/Resize/Tick/SigInt stay runner-owned (prompt_toolkit redraw, wait-clock,
    signal handler); gate-key effects arrive in chunk 3.
    """
    etype = (event or {}).get("type", "")
    data = (event or {}).get("data", {}) or {}
    if etype == "thinking" and str(data.get("text", "")).strip():
        model.append("thought", {"text": data["text"]})
    elif etype == "tool_call":
        model.append("tool", {"name": data.get("name", "?"),
                              "arguments": data.get("arguments", {}),
                              "status": "running"})
    elif etype == "tool_result":
        for b in reversed(model.blocks):
            if b.kind == "tool" and b.payload.get("status") == "running":
                b.payload.update({"status": "done",
                                  "summary": str(data.get("summary") or data.get("result") or "")[:120]})
                break
    elif etype in ("plan", "diff", "log", "gate"):
        model.append(etype, dict(data))
    return []
