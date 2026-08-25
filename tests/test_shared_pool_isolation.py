"""Loop close must never kill the process-global shared pool.

Reproduction of the E2E flakiness: each pytest-asyncio function-scoped loop
registered the shared pool as its default executor; asyncio shuts a loop's
default executor on close, so the *previous* test's loop close poisoned the
next test's to_thread with "cannot schedule new futures after shutdown".
"""

import asyncio

from wisp.async_utils import get_shared_executor, non_owning_executor


def _work(tag: str) -> str:
    return f"done-{tag}"


class TestNonOwningExecutor:
    def test_submit_and_map_delegate(self):
        proxy = non_owning_executor()
        assert proxy.submit(_work, "a").result() == "done-a"
        assert list(proxy.map(_work, ["m1", "m2"])) == ["done-m1", "done-m2"]

    def test_shutdown_is_noop(self):
        proxy = non_owning_executor()
        proxy.shutdown(wait=True, cancel_futures=True)
        # pool still alive
        assert proxy.submit(_work, "post").result() == "done-post"

    def test_loop_close_leaves_shared_pool_usable(self):
        """The exact poisoning sequence, twice in one process."""
        async def cycle(tag: str) -> str:
            loop = asyncio.get_running_loop()
            loop.set_default_executor(non_owning_executor())
            return await asyncio.to_thread(_work, tag)

        for tag in ("one", "two"):
            result = asyncio.run(cycle(tag))
            assert result == f"done-{tag}"
        # The second asyncio.run closed its loop (and pre-fix would have
        # shut the shared pool with it) — the pool must still serve.
        assert get_shared_executor().submit(_work, "final").result() == "done-final"

    def test_root_start_registers_proxy_not_raw_pool(self, tmp_path, monkeypatch):
        """CompositionRoot must hand loops the proxy, never the raw pool."""
        from wisp.composition import CompositionRoot
        from wisp.config import WispConfig

        cfg = WispConfig().replace(workspace=str(tmp_path))

        captured = {}

        async def main():
            loop = asyncio.get_running_loop()
            # Construct inside the loop — mirrors e2e/_root() and every
            # async consumer; __post_init__ binds the running loop.
            root = CompositionRoot(config=cfg)
            root.start()
            captured["executor"] = loop._default_executor
            out = await asyncio.to_thread(_work, "root")
            root.shutdown()
            return out

        assert asyncio.run(main()) == "done-root"
        from wisp.async_utils import NonOwningExecutor
        assert isinstance(captured["executor"], NonOwningExecutor), (
            "raw pool registered as loop default — loop.close() will kill it "
            "for every later root in this process"
        )
