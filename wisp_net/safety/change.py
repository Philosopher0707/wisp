"""A proposed network change: declarative gNMI operations plus what the change means to do.

A change set is desired state, not commands: each operation is an update or delete of an
OpenConfig path on one device. It also carries the intent in words, the reachability it
is *meant* to remove (so verification does not flag it as a regression), and the
proposer's confidence. Its fingerprint identifies it for idempotency and the ledger.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from wisp_net.paths import PathError, parse_path

MAX_OPS = 200


class ChangeError(ValueError):
    pass


@dataclass(frozen=True)
class Op:
    device: str
    path: str
    value: Any = None
    delete: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"device": self.device, "op": "delete" if self.delete else "update", "path": self.path}
        if not self.delete:
            out["value"] = self.value
        return out


@dataclass(frozen=True)
class ChangeSet:
    ops: tuple[Op, ...]
    intent: str
    expected_unreachable: tuple[tuple[str, str], ...] = ()
    confidence: float = 1.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChangeSet":
        if not isinstance(data, dict):
            raise ChangeError("a change set is an object with `intent` and `ops`")
        intent = str(data.get("intent", "")).strip()
        if not intent:
            raise ChangeError("`intent` is required: say what the change is for")
        raw_ops = data.get("ops")
        if not isinstance(raw_ops, list) or not raw_ops:
            raise ChangeError("`ops` must be a non-empty list")
        if len(raw_ops) > MAX_OPS:
            raise ChangeError(f"at most {MAX_OPS} operations per change set")
        ops = []
        for i, raw in enumerate(raw_ops):
            if not isinstance(raw, dict):
                raise ChangeError(f"ops[{i}] must be an object")
            kind = raw.get("op", "update")
            if kind not in ("update", "delete"):
                raise ChangeError(f"ops[{i}].op must be update or delete")
            device, path = str(raw.get("device", "")), str(raw.get("path", ""))
            if not device or not path:
                raise ChangeError(f"ops[{i}] needs `device` and `path`")
            try:
                parse_path(path)
            except PathError as exc:
                raise ChangeError(f"ops[{i}].path: {exc}") from None
            if kind == "update" and "value" not in raw:
                raise ChangeError(f"ops[{i}] is an update without a `value`")
            ops.append(Op(device, path, raw.get("value"), kind == "delete"))
        expected = []
        for pair in data.get("expected_unreachable", []) or []:
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                raise ChangeError("expected_unreachable entries are [source_device, prefix] pairs")
            expected.append((str(pair[0]), str(pair[1])))
        try:
            confidence = float(data.get("confidence", 1.0))
        except (TypeError, ValueError):
            raise ChangeError("`confidence` must be a number in 0..1") from None
        if not 0.0 <= confidence <= 1.0:
            raise ChangeError("`confidence` must be in 0..1")
        return cls(tuple(ops), intent, tuple(expected), confidence)

    def to_dict(self) -> dict[str, Any]:
        return {"intent": self.intent, "ops": [o.to_dict() for o in self.ops],
                "expected_unreachable": [list(p) for p in self.expected_unreachable],
                "confidence": self.confidence}

    @property
    def fingerprint(self) -> str:
        """Identity of what the change does (intent and confidence are not part of it)."""
        canonical = json.dumps([o.to_dict() for o in self.ops], sort_keys=True, separators=(",", ":"),
                               default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def by_device(self) -> dict[str, tuple[list[tuple[str, Any]], list[str]]]:
        out: dict[str, tuple[list[tuple[str, Any]], list[str]]] = {}
        for op in self.ops:
            updates, deletes = out.setdefault(op.device, ([], []))
            if op.delete:
                deletes.append(op.path)
            else:
                updates.append((op.path, op.value))
        return out

    def drained_ports(self) -> set[tuple[str, str]]:
        """Interfaces the change administratively shuts: taking those links down is its intent."""
        ports: set[tuple[str, str]] = set()
        for op in self.ops:
            path = parse_path(op.path)
            if (not op.delete and op.value is False and path[0].name == "interfaces" and path[1].key("name")
                    and path[-1].name == "enabled"):
                ports.add((op.device, str(path[1].key("name"))))
        return ports

    def disabled_neighbors(self) -> set[tuple[str, str]]:
        """(device, neighbor address) of BGP sessions the change administratively disables."""
        out: set[tuple[str, str]] = set()
        for op in self.ops:
            path = parse_path(op.path)
            if (not op.delete and op.value is False and path[-1].name == "enabled"
                    and any(e.name == "neighbor" for e in path)):
                neighbor = next(e for e in path if e.name == "neighbor").key("neighbor-address")
                if neighbor:
                    out.add((op.device, neighbor))
        return out

    def touched_ports(self) -> set[tuple[str, str]]:
        """Interfaces whose forwarding behaviour the change can alter (admin state, MTU, ACL binding)."""
        ports: set[tuple[str, str]] = set()
        for op in self.ops:
            path = parse_path(op.path)
            if path[0].name == "interfaces" and path[1].key("name"):
                ports.add((op.device, str(path[1].key("name"))))
            elif path[0].name == "acl" and len(path) > 2 and path[1].name == "interfaces" and path[2].key("id"):
                ports.add((op.device, str(path[2].key("id"))))
        return ports
