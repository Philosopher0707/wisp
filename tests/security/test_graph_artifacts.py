"""Artifact security: traversal, redaction, scoping, integrity.

THREAT: malicious type strings escape the store; secrets persist to disk/
SQLite/events; run B reads run A's artifacts; tampered files are consumed.
EXPECTED: containment + redact-at-rest + per-run scoping + hash verification.
"""

from __future__ import annotations

import json
import os

import pytest

from wisp.graph.artifacts import ArtifactStore, content_hash
from wisp.graph.store import GraphStore


def _stores(tmp_path):
    ws = str(tmp_path)
    return ArtifactStore(workspace=ws, store=GraphStore(workspace=ws))


class TestTraversal:
    @pytest.mark.parametrize("evil", ["../../evil", "..\\evil", "sub/dir", "/abs",
                                      "a/b/../../c", "..", ".", "x" * 65])
    def test_put_type_traversal_rejected(self, tmp_path, evil):
        s = _stores(tmp_path)
        with pytest.raises(ValueError):
            s.put("r1", "n1", evil, {"x": 1})

    def test_put_does_not_escape_store(self, tmp_path):
        s = _stores(tmp_path)
        art = s.put("r1", "n1", "report", {"x": 1})
        got = os.path.realpath(os.path.join(s.dir, art.artifact_id + ".json"))
        assert got.startswith(os.path.realpath(s.dir) + os.sep)
        assert os.path.dirname(got) == os.path.realpath(s.dir)

    @pytest.mark.parametrize("ref", ["artifact://../planted", "artifact://../../.env",
                                      "artifact:///etc/passwd", "artifact://sub/dir",
                                      "artifact://", "not-a-ref", "artifact://.."])
    def test_get_traversal_rejected(self, tmp_path, ref):
        s = _stores(tmp_path)
        with pytest.raises((ValueError, LookupError, OSError)):
            s.get(ref, "r1")

    def test_planted_file_unreadable(self, tmp_path):
        s = _stores(tmp_path)
        planted = os.path.join(s.dir, "planted.json")
        with open(planted, "w") as fh:
            json.dump({"artifact_id": "planted", "content": {"secret": 1}}, fh)
        # No metadata row -> invisible even with a well-formed ref.
        with pytest.raises((LookupError, ValueError)):
            s.get("artifact://planted", "r1")


class TestRedaction:
    SECRETS = ("OPENAI_API_KEY=sk-probe-1234567890abcdef",
               "Authorization: Bearer probe-token-abcdef123456",
               "AKIAIOSFODNN7EXAMPLE",
               "ghp_probetoken1234567890abcdef",
               "password: hunter2-hunter2")

    @pytest.mark.parametrize("secret", SECRETS)
    def test_secrets_redacted_at_rest(self, tmp_path, secret):
        s = _stores(tmp_path)
        art = s.put("r1", "n1", "report", {"blob": f"prefix {secret} suffix"})
        with open(os.path.join(s.dir, art.artifact_id + ".json")) as fh:
            raw = fh.read()
        assert secret not in raw
        assert "[REDACTED" in raw

    def test_events_redacted(self, tmp_path):
        import asyncio
        from .conftest import make_executor, ok_runner
        from wisp.graph.types import Graph, GraphNode, NodeType, NodeContract
        events = []
        g = Graph(id="t", entrypoint="a",
                  nodes=(GraphNode(id="a", type=NodeType.AGENT,
                                   contract=NodeContract(id="a")),), edges=())
        ex = make_executor(tmp_path, ok_runner(), emit=events.append)
        r = asyncio.run(ex.run(g, {"api_key": "OPENAI_API_KEY=sk-probe-1234567890abcdef"}))
        store = GraphStore(workspace=str(tmp_path))
        for row in store.events(r["run_id"]):
            assert "sk-probe" not in json.dumps(row)
        for e in events:
            assert "sk-probe" not in json.dumps(e)

    def test_private_key_redacted(self, tmp_path):
        s = _stores(tmp_path)
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBPROBE\n-----END RSA PRIVATE KEY-----"
        art = s.put("r1", "n1", "report", {"key": pem})
        assert s.get(art.uri, "r1")["key"].startswith("-----BEGIN")
        assert "MIIBPROBE" not in open(os.path.join(s.dir, art.artifact_id + ".json")).read()


class TestIsolationAndIntegrity:
    def test_cross_run_read_fails(self, tmp_path):
        s = _stores(tmp_path)
        art = s.put("run-A", "n1", "report", {"private": "A"})
        with pytest.raises(LookupError):
            s.get(art.uri, "run-B")

    def test_tampered_content_rejected(self, tmp_path):
        s = _stores(tmp_path)
        art = s.put("r1", "n1", "report", {"v": 1})
        path = os.path.join(s.dir, art.artifact_id + ".json")
        with open(path) as fh:
            doc = json.load(fh)
        doc["content"] = {"v": 2, "forged": True}
        with open(path, "w") as fh:
            json.dump(doc, fh)
        with pytest.raises(ValueError, match="integrity"):
            s.get(art.uri, "r1")

    def test_artifact_bomb_rejected(self, tmp_path):
        s = _stores(tmp_path)
        with pytest.raises(ValueError, match="too large"):
            s.put("r1", "n1", "report", {"blob": "x" * 5_000_000})

    def test_hash_is_content_addressed(self):
        assert content_hash({"a": 1}) == content_hash({"a": 1})
        assert content_hash({"a": 1}) != content_hash({"a": 2})
