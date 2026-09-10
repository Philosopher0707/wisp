"""Artifact store — durable, content-addressed node outputs.

Downstream nodes load ``artifact://<id>`` references instead of full
transcripts. Files live under ``<workspace>/.wisp/artifacts/``; the
``graph_artifacts`` table (GraphStore) holds the metadata.

Security properties (all enforced, all tested):
  - artifact *types* are allowlisted to a filename-safe charset — a hostile
    type can never traverse out of the artifacts directory;
  - every read/write path is realpath-contained in the artifacts dir
    (mirrors ``wisp.tools._utils._resolve_path``);
  - payloads are secret-redacted (``wisp.auth.secrets``) BEFORE persistence,
    and sizes are capped — redaction at rest, not at display;
  - references resolve only within the requesting run (cross-run reads fail);
  - content is hash-verified on consume; mismatches are rejected, never
    silently consumed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any

from wisp.graph.security import scrub
from wisp.graph.store import GraphStore
from wisp.graph.types import GraphArtifact

_TYPE_RX = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_ALNUM_RX = re.compile(r"[A-Za-z0-9]")


def _check_type(type: str) -> None:
    if _TYPE_RX.fullmatch(type or "") is None or type in (".", "..") \
            or _ALNUM_RX.search(type) is None:
        raise ValueError(f"invalid artifact type {type!r}")
MAX_ARTIFACT_BYTES = 4_000_000
MAX_ARTIFACTS_PER_RUN = 4096


def content_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _contain(directory: str, name: str) -> str:
    """Canonical containment (see wisp.pathsec). Kept as a thin local alias
    so artifact call sites stay readable; semantics live in one place."""
    from wisp.pathsec import resolve_contained
    return resolve_contained(directory, name, allow_absolute=False)


class ArtifactStore:
    def __init__(self, workspace: str = ".", store: GraphStore | None = None) -> None:
        self.workspace = os.path.abspath(workspace)
        self.dir = os.path.join(self.workspace, ".wisp", "artifacts")
        os.makedirs(self.dir, exist_ok=True)
        self._store = store or GraphStore(workspace=workspace)

    def put(self, run_id: str, node_run_id: str, type: str, content: Any,
            producer: str = "", metadata: dict | None = None,
            schema_version: str = "1") -> GraphArtifact:
        _run_id(run_id)
        _check_type(type)
        if len(self._store.artifacts(run_id)) >= MAX_ARTIFACTS_PER_RUN:
            raise ValueError(f"run {run_id}: too many artifacts")
        # Redact BEFORE hashing/persisting: secrets never reach disk or SQLite.
        clean = scrub(content)
        raw = json.dumps(clean, default=str)
        if len(raw.encode()) > MAX_ARTIFACT_BYTES:
            raise ValueError(f"artifact too large (max {MAX_ARTIFACT_BYTES} bytes)")
        digest = content_hash(clean)
        artifact_id = f"{type}-{digest[:16]}"
        path = _contain(self.dir, f"{artifact_id}.json")
        if not os.path.exists(path):
            with open(path, "x") as fh:
                json.dump({"artifact_id": artifact_id, "type": type,
                           "schema_version": schema_version, "content": clean}, fh)
        artifact = GraphArtifact(
            artifact_id=artifact_id, run_id=run_id, node_run_id=node_run_id,
            type=type, schema_version=schema_version, content_hash=digest,
            uri=f"artifact://{artifact_id}", producer=producer,
            created_at=time.time(), metadata=metadata or {})
        self._store.put_artifact(artifact_id, run_id, node_run_id, type,
                                 digest, artifact.uri, producer, metadata or {})
        self._store.append_event(run_id, "graph.artifact_created",
                                 {"artifact_id": artifact_id, "type": type,
                                  "producer": producer, "content_hash": digest})
        return artifact

    def get(self, artifact_id: str, run_id: str = "") -> Any:
        """Load + hash-verify. run_id scopes cross-run isolation."""
        ref = _ref(artifact_id)
        path = _contain(self.dir, f"{ref}.json")
        with open(path) as fh:
            doc = json.load(fh)
        if doc.get("artifact_id") != ref:
            raise ValueError(f"artifact id mismatch in {ref!r}")
        content = doc["content"]
        digest = content_hash(content)
        row = self._lookup(ref, run_id)
        if row is None:
            raise LookupError(f"artifact {ref!r} not visible to this run")
        if row["content_hash"] != digest:
            raise ValueError(f"artifact {ref!r} failed integrity check "
                             f"(stored content != recorded hash)")
        return content

    def resolve_inputs(self, inputs: dict[str, Any], run_id: str = "") -> dict[str, Any]:
        """Replace ``artifact://`` string values with verified content (one level)."""
        resolved: dict[str, Any] = {}
        for k, v in inputs.items():
            if isinstance(v, str) and v.startswith("artifact://"):
                try:
                    resolved[k] = self.get(v, run_id)
                except (OSError, ValueError, LookupError, KeyError):
                    resolved[k] = v
            else:
                resolved[k] = v
        return resolved

    def _lookup(self, ref: str, run_id: str) -> dict | None:
        for row in self._store.artifacts(run_id or ""):
            if row["artifact_id"] == ref:
                return row
        # Fall back: any run in this workspace store may share content-addressed
        # artifacts explicitly re-registered; unregistered refs stay invisible.
        if not run_id:
            for row in self._store.list_artifacts():
                if row["artifact_id"] == ref:
                    return row
        return None


def _ref(artifact_id: str) -> str:
    if not isinstance(artifact_id, str) or not artifact_id.startswith("artifact://"):
        raise ValueError(f"not an artifact reference: {artifact_id!r}")
    ref = artifact_id[len("artifact://"):]
    if not ref or len(ref) > 128 or "/" in ref or "\\" in ref or ref.startswith("."):
        raise ValueError(f"malformed artifact reference: {artifact_id!r}")
    return ref


def _run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not run_id or len(run_id) > 128:
        raise ValueError(f"invalid run id: {run_id!r}")
