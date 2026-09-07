"""Hook command substitution must be shell-safe (F3).

Hook commands run with shell=True, so substituted values (workspace,
session_id, tool_name) must be quoted — otherwise a directory named
`$(...)` executes arbitrary code at session start.
"""

import shlex

from wisp.infra.hook_types import HookManager


def _mgr():
    return HookManager(config_dir=None, workspace="/ws")


class TestSubstituteQuoting:
    def test_workspace_with_spaces_is_single_word(self):
        m = _mgr()
        out = m._substitute_command("cd {workspace} && make", {"workspace": "/my ws/proj"})
        assert out == f"cd {shlex.quote('/my ws/proj')} && make"

    def test_command_substitution_is_neutered(self):
        m = _mgr()
        evil = "/x/$(touch /tmp/pwned)"
        out = m._substitute_command("ls {workspace}", {"workspace": evil})
        assert out == f"ls {shlex.quote(evil)}"
        # The payload must not survive as executable shell syntax.
        assert "$(" not in out or out.startswith("ls '")

    def test_plain_paths_unchanged(self):
        m = _mgr()
        out = m._substitute_command("cd {workspace}", {"workspace": "/plain/path"})
        assert out == "cd /plain/path"

    def test_all_placeholders_substituted(self):
        m = _mgr()
        out = m._substitute_command(
            "{tool_name} {event} {workspace} {session_id}",
            {"tool_name": "run_bash", "event": "pre", "workspace": "/w s", "session_id": "s1"},
        )
        assert out == f"run_bash pre {shlex.quote('/w s')} s1"
