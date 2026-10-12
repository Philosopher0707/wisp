"""`wisp graph` verbs take the orchestrator they run on from the host when there is one, and never build a second root inside the REPL."""

from __future__ import annotations

from wisp.graph.cli import _workers


def test_a_host_orchestrator_is_used_as_it_is(monkeypatch):
    built: list[int] = []
    import wisp.composition as composition
    monkeypatch.setattr(composition, "CompositionRoot", lambda *a, **k: built.append(1))
    host = object()
    with _workers(host, ".", "", "") as workers:
        assert workers is host
    assert built == []


def test_a_root_that_cannot_be_built_leaves_the_graph_runnable_and_says_so(monkeypatch, capsys):
    import wisp.composition as composition

    def boom(*a, **k):
        raise ValueError("no provider configured")

    monkeypatch.setattr(composition, "CompositionRoot", boom)
    with _workers(None, ".", "", "") as workers:
        assert workers is None
    assert "agent nodes will only be able to read" in capsys.readouterr().err


def test_slash_graph_in_the_repl_hands_over_the_repls_own_orchestrator(monkeypatch):
    """Through the real dispatcher: `/graph run ...` runs on `runtime.orchestrator` (already holding the person's approver), it does not build another root."""
    from types import SimpleNamespace

    import wisp.graph.cli as graph_cli
    from wisp.cli.dispatcher import Dispatcher, ReplContext

    seen: dict = {}

    def fake_main(argv, workspace=".", model="", provider="", orchestrator=None):
        seen.update(argv=argv, orchestrator=orchestrator)
        return 0

    monkeypatch.setattr(graph_cli, "main", fake_main)
    host = object()
    ctx = ReplContext(runtime=SimpleNamespace(orchestrator=host), transport=None, session={}, config={"workspace": "."})
    Dispatcher().dispatch(ctx, "/graph run g.yaml {}")
    assert seen["orchestrator"] is host and seen["argv"][:2] == ["run", "g.yaml"]


def test_from_the_shell_the_terminals_subagent_approver_is_the_one_wired(monkeypatch):
    """`wisp graph run` gives the workers the transport's subagent approval (the one that stops the spinner it starts), not its plain `approve`."""
    from types import SimpleNamespace

    import wisp.composition as composition
    import wisp.transport.cli as transport_cli

    given: list = []

    class FakeRoot:
        def __init__(self, config):
            self.runtime = object()
            self.subagent_orchestrator = SimpleNamespace(set_approval_handler=given.append)
            self.background_agents = None

        def start(self):
            pass

        def shutdown(self):
            pass

    class FakeTransport:
        def __init__(self, *a, **k):
            pass

        async def approve(self, tool_call):
            return True

        async def approve_for_subagent(self, tool_call):
            return True

    monkeypatch.setattr(composition, "CompositionRoot", FakeRoot)
    monkeypatch.setattr(transport_cli, "CLITransport", FakeTransport)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    with _workers(None, ".", "", ""):
        pass
    assert len(given) == 1 and given[0].__name__ == "approve_for_subagent"


def test_without_a_terminal_the_workers_get_no_approver(monkeypatch):
    from types import SimpleNamespace

    import wisp.composition as composition

    given: list = []

    class FakeRoot:
        def __init__(self, config):
            self.runtime = object()
            self.subagent_orchestrator = SimpleNamespace(set_approval_handler=given.append)

        def start(self):
            pass

        def shutdown(self):
            pass

    monkeypatch.setattr(composition, "CompositionRoot", FakeRoot)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with _workers(None, ".", "", "") as workers:
        assert workers is not None
    assert given == []
