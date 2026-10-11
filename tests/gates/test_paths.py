"""Layer 1. Nothing is written outside the workspace, however the path is spelled, indirected or hidden."""

from __future__ import annotations

import os

import pytest

from tests.gates.conftest import rules
from wisp.core.gates import GateMode, check_command, check_tool_args, check_tool_call


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    return os.path.realpath(d)


class TestShellWrites:
    @pytest.mark.parametrize("command", [
        "echo x > /etc/hosts", "echo x >> /etc/hosts", "echo x > ../escape.txt", "cp a /etc/passwd", "mv a /tmp/elsewhere", "rm /etc/hosts", "touch /outside", "mkdir /newdir",
        "tee /etc/x < in", "sed -i s/a/b/ /etc/hosts", "chmod 777 /etc/hosts", "chown me /etc/hosts", "ln -s x /etc/link", "dd if=a of=/etc/x", "truncate -s 0 /etc/hosts",
        "install -m 755 a /usr/local/bin/a", "rsync -a src/ /elsewhere/", "unzip -q a.zip -d /elsewhere", "cp -t /elsewhere a", "mv -t /elsewhere a", "echo x &> /etc/y",
        "git -C /elsewhere status", "git --git-dir=/elsewhere/.git log", "cd .. && rm x", "cd /etc && rm hosts", "cd /etc; echo x > hosts",
    ])
    def test_writes_outside_the_workspace_are_refused(self, ctx, command):
        found = check_command(command, ctx)
        assert "OUTSIDE_WORKSPACE" in rules(found), (command, [v.render() for v in found])

    @pytest.mark.parametrize("command", [
        "echo x > out.txt", "echo x > sub/out.txt", "echo x > ./sub/../out.txt", "touch a b", "mkdir -p x/y/z", "cp a b", "mv a sub/b", "rm file", "tee log < in", "sed -i s/a/b/ f.py", "dd if=a of=out.bin",
        "echo x > /dev/null", "cmd 2>/dev/null", "cmd >/dev/stderr", "cd sub && echo x > out", "(cd sub && touch f)", "cd sub; cd .. ; touch f", "cp /etc/hosts local.txt", "cat /etc/hosts > copy.txt",
        "git -C sub status", "rsync -a src/ dest/", "ln -s target link",
    ])
    def test_writes_inside_the_workspace_are_allowed(self, ctx, command):
        found = [v for v in check_command(command, ctx) if v.layer == "path"]
        assert not found, (command, [v.render() for v in found])

    def test_the_workspace_root_itself_is_inside(self, ctx):
        assert not [v for v in check_command("touch .", ctx) if v.layer == "path"]

    @pytest.mark.parametrize("command", ["echo x > $HOME/x", "rm $X", "echo x > ${DEST}/f", "echo x > $(pwd)/../f", "touch `echo /etc/x`", "cd $X && touch f", "cd - && touch f", "a || cd /x; touch f"])
    def test_a_target_that_cannot_be_resolved_is_refused_not_guessed(self, ctx, command):
        assert "OUTSIDE_WORKSPACE" in rules(check_command(command, ctx)), command

    def test_tilde_is_the_home_directory_not_the_workspace(self, ctx):
        assert "OUTSIDE_WORKSPACE" in rules(check_command("echo x > ~/f", ctx))
        assert "OUTSIDE_WORKSPACE" in rules(check_command("echo x > ~root/f", ctx))

    def test_globs_are_judged_by_their_directory(self, ctx):
        assert "OUTSIDE_WORKSPACE" in rules(check_command("rm /etc/*.conf", ctx))
        assert not [v for v in check_command("rm sub/*.pyc", ctx) if v.layer == "path"]

    def test_a_subshell_cd_does_not_leak_out_of_its_group(self, ctx):
        assert not [v for v in check_command("(cd /etc && ls); touch f", ctx) if v.layer == "path"]
        assert "OUTSIDE_WORKSPACE" in rules(check_command("cd /etc; touch f", ctx))

    def test_a_pipeline_stage_cd_does_not_change_the_next_commands_directory(self, ctx):
        assert not [v for v in check_command("cd /etc | cat; touch f", ctx) if v.layer == "path"]


class TestSymlinksAndPrefixes:
    def test_a_symlink_inside_the_workspace_pointing_out_is_refused(self, ws, outside, ctx):
        os.symlink(outside, os.path.join(ws, "link"))
        assert "OUTSIDE_WORKSPACE" in rules(check_command("echo x > link/f", ctx))
        assert "OUTSIDE_WORKSPACE" in rules(check_command("rm link/f", ctx))
        assert "OUTSIDE_WORKSPACE" in rules(check_command("cd link && touch f", ctx))

    def test_a_symlink_to_a_file_outside_is_refused(self, ws, outside, ctx):
        target = os.path.join(outside, "secret.txt")
        open(target, "w").close()
        os.symlink(target, os.path.join(ws, "alias.txt"))
        assert "OUTSIDE_WORKSPACE" in rules(check_command("echo x > alias.txt", ctx))

    def test_a_symlink_that_stays_inside_is_fine(self, ws, ctx):
        os.symlink(os.path.join(ws, "sub"), os.path.join(ws, "inner"))
        assert not [v for v in check_command("echo x > inner/f", ctx) if v.layer == "path"]

    def test_a_sibling_directory_sharing_the_workspace_prefix_is_outside(self, ws, ctx):
        sibling = ws + "-sibling"
        os.makedirs(sibling)
        assert "OUTSIDE_WORKSPACE" in rules(check_command(f"touch {sibling}/f", ctx))

    def test_dotdot_that_returns_inside_is_resolved_not_rejected(self, ctx):
        assert not [v for v in check_command("touch sub/../ok", ctx) if v.layer == "path"]

    def test_a_missing_target_is_judged_by_where_it_would_be(self, ctx):
        assert not [v for v in check_command("touch does/not/exist/yet", ctx) if v.layer == "path"]
        assert "OUTSIDE_WORKSPACE" in rules(check_command("touch /does/not/exist/yet", ctx))


class TestWhitelistAndProtected:
    def test_an_explicit_extra_root_is_writable(self, make_ctx, outside):
        c = make_ctx(extra_write_roots=(outside,))
        assert not [v for v in check_command(f"echo x > {outside}/f", c) if v.layer == "path"]
        assert "OUTSIDE_WORKSPACE" in rules(check_command("echo x > /etc/f", c))

    def test_hook_and_git_config_paths_are_protected_even_inside(self, ctx):
        assert "OUTSIDE_WORKSPACE" in rules(check_command("echo x > .git/hooks/pre-commit", ctx))
        assert "OUTSIDE_WORKSPACE" in rules(check_command("echo x > .wisp/hooks/h.json", ctx))
        assert "OUTSIDE_WORKSPACE" in rules(check_command("cp a .git/config", ctx))

    def test_only_safe_devices_are_writable(self, ctx):
        for dev in ("/dev/null", "/dev/stderr", "/dev/stdout"):
            assert not [v for v in check_command(f"echo x > {dev}", ctx) if v.layer == "path"]
        assert check_command("echo x > /dev/sda", ctx)


class TestToolArguments:
    @pytest.mark.parametrize("path", ["/etc/passwd", "../outside.txt", "sub/../../x", "~/.ssh/authorized_keys"])
    def test_write_tool_paths_outside_are_refused(self, ctx, path):
        found = check_tool_args("write_file", {"path": path, "content": "x"}, ctx)
        assert "OUTSIDE_WORKSPACE" in rules(found), path

    @pytest.mark.parametrize("key", ["path", "new_path", "dest", "target"])
    def test_every_path_bearing_argument_is_checked(self, ctx, key):
        assert "OUTSIDE_WORKSPACE" in rules(check_tool_args("fs_mutate", {key: "/etc/x"}, ctx))

    def test_a_tool_path_is_literal_not_shell_expanded(self, ctx):
        # `write_file(path="$HOME/x")` names a directory called "$HOME" inside the workspace; nothing expands it. A SHELL command
        # that says `> $HOME/x` is a different matter and is refused above.
        assert not check_tool_args("write_file", {"path": "$HOME/x"}, ctx)

    def test_inside_paths_are_allowed(self, ctx, ws):
        assert not check_tool_args("write_file", {"path": "sub/a.py", "content": "x"}, ctx)
        assert not check_tool_args("write_file", {"path": os.path.join(ws, "a.py"), "content": "x"}, ctx)

    def test_a_symlink_escape_through_a_tool_path_is_refused(self, ws, outside, ctx):
        os.symlink(outside, os.path.join(ws, "link"))
        assert "OUTSIDE_WORKSPACE" in rules(check_tool_args("write_file", {"path": "link/f.txt"}, ctx))

    def test_read_only_tools_are_not_path_gated(self, ctx):
        d = check_tool_call("read_file", {"path": "/etc/hosts"}, ctx, mutating=False)
        assert d.allowed and not d.violations

    def test_control_characters_in_a_path_are_refused(self, ctx):
        assert check_tool_args("write_file", {"path": "a\x01b"}, ctx)
        assert check_tool_args("write_file", {"path": "a\nb"}, ctx)


def test_observe_mode_reports_but_does_not_block(make_ctx):
    c = make_ctx(mode=GateMode.OBSERVE)
    d = check_tool_call("run_bash", {"command": "rm -rf x"}, c, mutating=True)
    assert d.allowed is True and d.violations and d.observed_only
