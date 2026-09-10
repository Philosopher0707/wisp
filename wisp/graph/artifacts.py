"""Artifact store — durable, content-addressed node outputs.

Downstream nodes load ``artifact://<id>`` references instead of full
transcripts. Files live under ``<workspace>/.wisp/artifacts/``; the
``graph_artifacts`` table (GraphStore) holds the metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from wisp.graph.store import GraphStore
from wisp.graph.types import GraphArtifact


def content_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


class ArtifactStore:
    def __init__(self, workspace: str = ".", store: GraphStore | None = None) -> None:
        self.workspace = os.path.abspath(workspace)
        self.dir = os.path.join(self.workspace, ".wisp", "artifacts")
        os.makedirs(self.dir, exist_ok=True)
        self._store = store or GraphStore(workspace=workspace)

    def put(self, run_id: str, node_run_id: str, type: str, content: Any,
            producer: str = "", metadata: dict | None = None,
            schema_version: str = "1") -> GraphArtifact:
        digest = content_hash(content)
        artifact_id = f"{type}-{digest[:12]}"
        path = os.path.join(self.dir, f"{artifact_id}.json")
        if not os.path.exists(path):
            with open(path, "w") as fh:
                json.dump({"artifact_id": artifact_id, "type": type,
                           "schema_version": schema_version, "content": content}, fh)
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

    def get(self, artifact_id: str) -> Any:
        ref = artifact_id.replace("artifact://", "")
        path = os.path.join(self.dir, f"{ref}.json")
        with open(path) as fh:
            return json.load(fh)["content"]

    def resolve_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Replace ``artifact://`` string values with loaded content (one level)."""
        resolved: dict[str, Any] = {}
        for k, v in inputs.items():
            if isinstance(v, str) and v.startswith("artifact://"):
                try:
                    resolved[k] = self.get(v)
                except OSError:
                    resolved[k] = v
            else:
                resolved[k] = v
        return resolved
