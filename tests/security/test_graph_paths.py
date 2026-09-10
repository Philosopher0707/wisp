"""Path / workspace attacks: traversal, symlinks, unicode, cross-workspace.

THREAT: malicious node ids, run ids, artifact refs, workspaces escape
confinement or collide across trust domains.
EXPECTED: charset validation, realpath containment, per-workspace stores.
"""

from __future__ import annotations

import os

import pytest

from wisp.graph.artifacts import ArtifactStore
from wisp.graph.store import GraphStore
from wisp.graph.types import Graph, GraphNode, GraphPolicy, NodeContract, NodeType
from .conftest import make_executor, ok_runner


def _agent(nid):
    return GraphNode(id=nid, type=NodeType.AGENT, contract=NodeContract(id=nid))


class TestRunIdHygiene:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("rid", ["../evil", "a/b", "x" * 129, "a:b"])
    async def test_malicious_run_id_rejected(self, tmp_path, rid):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=())
        ex = make_executor(tmp_path, ok_runner())
        with pytest.raises(ValueError, match="run id"):
            await ex.run(g, {}, run_id=rid)

    @pytest.mark.asyncio
    async def test_empty_run_id_generates_one(self, tmp_path):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=())
        ex = make_executor(tmp_path, ok_runner())
        r = await ex.run(g, {})
        assert r["run_id"].startswith("graph-")

    @pytest.mark.asyncio
    async def test_run_id_collision_is_clean_error(self, tmp_path):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=())
        ex = make_executor(tmp_path, ok_runner())
        r = await ex.run(g, {}, run_id="dup")
        assert r["status"] == "succeeded"
        with pytest.raises(ValueError, match="cannot create run"):
            await ex.run(g, {}, run_id="dup")


class TestWorkspaceConfinement:
    def test_artifact_store_contained(self, tmp_path):
        s = ArtifactStore(workspace=str(tmp_path))
        assert os.path.realpath(s.dir).startswith(os.path.realpath(str(tmp_path)))

    def test_symlinked_workspace_resolves(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        try:
            os.symlink(str(real), str(link))
        except OSError:
            pytest.skip("symlinks unavailable")
        s = ArtifactStore(workspace=str(link))
        # realpath containment: files land under the real dir, not elsewhere.
        art = s.put("r1", "n1", "report", {"x": 1})
        assert os.path.realpath(os.path.join(s.dir, art.artifact_id + ".json")).startswith(
            os.path.realpath(str(real)))

    @pytest.mark.asyncio
    async def test_policy_workspace_symlink_bypass_blocked(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=(),
                  policies=GraphPolicy(workspace=str(outside)))
        ex = make_executor(tmp_path, ok_runner())
        with pytest.raises(PermissionError):
            await ex.run(g, {})

    def test_db_lives_under_workspace(self, tmp_path):
        GraphStore(workspace=str(tmp_path))
        assert os.path.exists(str(tmp_path / ".wisp" / "wisp.db"))
