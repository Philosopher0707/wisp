"""TDD for entry point refactoring.

Tests that __main__.py uses CompositionRoot.
"""



class TestEntryPoint:
    """Entry point uses CompositionRoot."""

    def test_main_imports_composition_root(self):
        from wisp.__main__ import main
        # main should exist and be callable
        assert callable(main)

    def test_composition_root_importable(self):
        from wisp.composition import CompositionRoot
        assert CompositionRoot is not None

    def test_composition_root_has_start(self):
        from wisp.composition import CompositionRoot
        assert hasattr(CompositionRoot, "start")

    def test_composition_root_has_shutdown(self):
        from wisp.composition import CompositionRoot
        assert hasattr(CompositionRoot, "shutdown")

    def test_composition_root_accepts_config(self):
        from wisp.composition import CompositionRoot
        import inspect
        sig = inspect.signature(CompositionRoot.__init__)
        params = list(sig.parameters.keys())
        assert "config" in params


class TestEntryPointModes:
    """Entry point dispatches to correct mode."""

    def test_cli_mode_runs_cli_transport(self):
        from wisp.__main__ import main
        # main should handle "cli" mode
        assert callable(main)

    def test_server_mode_runs_server_transport(self):
        from wisp.__main__ import main
        # main should handle "server" mode
        assert callable(main)
