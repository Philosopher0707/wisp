"""12.5D: one-shot dispatch goes through _SUBCOMMAND_TABLE.

THREAT: a command bypassing the table (stale elif, shadowed name).
EXPECTED: table keys == known subcommands; every command has help;
unknown input still reaches implicit mode.
"""

from __future__ import annotations

import re


def _table_names():
    src = open("wisp/__main__.py", errors="replace").read()
    block = src.split("_SUBCOMMAND_TABLE = {", 1)[1].split("}", 1)[0]
    return re.findall(r'"([a-z_]+)"', block)


def _elif_names():
    src = open("wisp/__main__.py", errors="replace").read()
    return re.findall(r'elif first == "([a-z_]+)":', src)


class TestDispatchTable:
    def test_no_elif_dispatch_remains(self):
        assert _elif_names() == [], f"stale elif branches: {_elif_names()}"

    def test_table_covers_known_subcommands(self):
        from wisp.__main__ import _SUBCOMMAND_NAMES
        assert sorted(_table_names()) == sorted(_SUBCOMMAND_NAMES)

    def test_every_command_has_help(self):
        from wisp.__main__ import _SUBCOMMAND_HELP, print_subcommand_help
        import io
        from contextlib import redirect_stdout
        for name in _table_names():
            assert name in _SUBCOMMAND_HELP, f"no help for {name}"
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert print_subcommand_help(name) is True
            assert buf.getvalue().strip()

    def test_unknown_subcommand_help_fails_closed(self):
        from wisp.__main__ import print_subcommand_help
        assert print_subcommand_help("frobnicate-nope") is False
