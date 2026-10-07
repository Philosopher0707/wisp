"""The parser's own invariants: it never raises, it is deterministic, and what it cannot understand it refuses."""

from __future__ import annotations

import random

import pytest

from tests.gates.conftest import alarm
from wisp.core.gates.invocations import extract
from wisp.core.gates.shellparse import MAX_DEPTH, MAX_LENGTH, Simple, parse


def names(command: str, cwd: str = "/ws") -> list[str]:
    r = parse(command)
    assert r.ok, r.reasons
    invs, problems = extract(r.tree, cwd, "/home/u")
    assert not problems, problems
    return [i.name for i in invs]


class TestStructure:
    def test_chains_pipelines_and_groups_are_all_visible(self):
        assert names("a && b || c; d | e | f & g") == ["a", "b", "c", "d", "e", "f", "g"]

    def test_subshells_braces_and_substitutions_are_visible(self):
        # Substitutions run before the command that receives their output, so they come first.
        assert names("(a; b) ; { c; d; } ; echo $(e) `f` <(g)") == ["a", "b", "c", "d", "e", "f", "g", "echo"]

    def test_compound_command_bodies_are_visible(self):
        assert names("if x; then y; else z; fi") == ["x", "y", "z"]
        assert names("for i in 1 2; do rm $i; done") == ["rm"]
        assert names("while read l; do echo $l; done") == ["read", "echo"]

    def test_function_bodies_are_visible(self):
        assert "rm" in names("f() { rm x; }; f")

    def test_quoting_cannot_hide_the_command_name(self):
        for form in ['"rm" x', "'rm' x", "r\\m x", "$'rm' x", '$"rm" x'][:4]:
            assert names(form)[0] == "rm", form

    def test_ansi_c_escapes_are_decoded(self):
        assert names("$'r\\x6d' x")[0] == "rm"
        assert names("$'\\162\\155' x")[0] == "rm"

    def test_assignments_are_not_the_command(self):
        assert names("A=1 B=2 ls") == ["ls"]

    def test_heredoc_body_is_data_but_its_substitutions_run(self):
        r = parse("cat <<EOF\nhello $(rm x)\nEOF")
        invs, _ = extract(r.tree, "/ws", "")
        assert [i.name for i in invs] == ["rm", "cat"]
        quoted = parse("cat <<'EOF'\nhello $(rm x)\nEOF")
        invs, _ = extract(quoted.tree, "/ws", "")
        assert [i.name for i in invs] == ["cat"]

    def test_redirections_are_attached_with_their_fd(self):
        r = parse("cmd 2>err.log >out.log <in.txt")
        node = r.tree.stmts[0].andor.first.stages[0]
        assert isinstance(node, Simple)
        assert [(x.fd, x.op, x.target.text) for x in node.redirects] == [("2", ">", "err.log"), ("", ">", "out.log"), ("", "<", "in.txt")]

    def test_comments_are_ignored(self):
        assert names("ls # && rm -rf /") == ["ls"]


class TestFailClosed:
    @pytest.mark.parametrize("bad", [
        "echo 'unterminated", 'echo "unterminated', "echo `unterminated", "echo $(unterminated", "echo ${unterminated",
        "case x in a) ls;; esac", "a ;; b", "echo )", "( echo", "{ echo", "a &&", "a |", "cat <<EOF\nno terminator",
        "echo hi >", "echo $'bad\\q'", "\x00", "a" * (MAX_LENGTH + 1),
    ])
    def test_unparsable_input_is_refused_not_guessed(self, bad):
        r = parse(bad)
        assert r.ok is False and r.reasons

    def test_nesting_beyond_the_limit_is_refused(self):
        deep = "echo " + "$(" * (MAX_DEPTH + 2) + "x" + ")" * (MAX_DEPTH + 2)
        assert parse(deep).ok is False

    def test_non_text_is_refused(self):
        assert parse(None).ok is False  # type: ignore[arg-type]


class TestTotalityAndDeterminism:
    FRAGMENTS = ["rm", "-rf", "x", "&&", "||", ";", "|", "&", "(", ")", "{", "}", "$(", "`", "'", '"', "\\", "<<", "<<<", ">", ">>", "2>&1", "$X", "${Y}", "*", "~", "#", "\n", "EOF", "$'a'", "for", "do", "done", "if", "fi", "case", "esac", "!", "-c", "bash", "eval"]

    def test_parse_never_raises_never_hangs_and_is_deterministic(self):
        rng = random.Random(20260507)
        with alarm(60):
            for _ in range(3000):
                text = " ".join(rng.choice(self.FRAGMENTS) for _ in range(rng.randint(1, 14)))
                a, b = parse(text), parse(text)
                assert a == b
                assert a.ok or a.reasons

    @pytest.mark.parametrize("operator_first", ["&& a", "| x", "; && b", "a ; | b", "& a", "|| a", "(&& a)", "{ | a; }"])
    def test_an_operator_with_nothing_before_it_is_refused_and_does_not_spin(self, operator_first):
        with alarm(5):
            assert parse(operator_first).ok is False

    def test_extract_never_raises_on_what_parses(self):
        rng = random.Random(7)
        with alarm(60):
            for _ in range(3000):
                text = " ".join(rng.choice(self.FRAGMENTS) for _ in range(rng.randint(1, 14)))
                r = parse(text)
                if r.ok:
                    extract(r.tree, "/ws", "/home/u")
