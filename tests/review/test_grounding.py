"""A model finding is a claim. It becomes a finding only when the code it quotes is in the diff; the line number is then the harness's, never the model's."""

from __future__ import annotations

import pytest

from tests.review.helpers import added_file, edited_file, fake_token, files_of, make_diff
from wisp.review import grounding as G
from wisp.review.rules import Rule, Rules
from wisp.review.types import Severity

OLD = ["import os", "", "def load(path):", "    data = open(path).read()", "    return data"]
NEW = ["import os", "", "def load(path):", "    data = eval(open(path).read())", "    return data", "", "def other():", "    return 1"]


def by_path(files):
    return {f.path: f for f in files}


def claim(**kw):
    base = dict(file="app.py", quote="data = eval(open(path).read())", severity="warn", message="eval on file content", suggestion="use json.loads", rule="", lens="security")
    base.update(kw)
    return G.Claim(**base)


@pytest.fixture
def files():
    return by_path(edited_file("app.py", OLD, NEW))


def test_a_quoted_added_line_becomes_a_finding_at_the_harness_line_not_the_models(files):
    finding = G.ground(claim(), files)
    assert finding is not None and (finding.file, finding.line, finding.severity) == ("app.py", 4, Severity.WARN)
    assert finding.quote == "data = eval(open(path).read())" and finding.rule == "model:security"
    assert "grounded" in finding.evidence and "app.py:4" in finding.evidence


def test_the_models_own_line_number_is_not_even_read():
    assert not hasattr(G.Claim(file="a", quote="q", severity="warn", message="m", suggestion="", rule="", lens="x"), "line")


@pytest.mark.parametrize("quote", ["+    data = eval(open(path).read())", "data   =  eval( open(path).read() )", "    data = eval(open(path).read())\n"])
def test_a_diff_marker_or_extra_whitespace_in_the_quote_is_tolerated(files, quote):
    assert G.ground(claim(quote=quote), files) is not None


def test_a_two_line_quote_is_found_when_the_lines_are_consecutive(files):
    finding = G.ground(claim(quote="def other():\n    return 1"), files)
    assert finding is not None and finding.line == 7


def test_a_two_line_quote_across_a_gap_is_not_found(files):
    assert G.ground(claim(quote="import os\ndef load(path):"), files) is None


@pytest.mark.parametrize("name", ["b/app.py", "a/app.py", "./app.py", "  app.py  "])
def test_a_path_with_a_diff_prefix_or_padding_is_understood(files, name):
    assert G.ground(claim(file=name), files) is not None


def test_a_quote_cannot_be_stitched_from_the_ends_of_two_far_apart_hunks():
    old = [f"line {i}" for i in range(1, 41)]
    new = list(old)
    new[1] = "first_hunk_end_marker = 1"
    new[29] = "second_hunk_start_marker = 2"
    files = by_path(edited_file("long.py", old, new))
    assert G.ground(claim(file="long.py", quote="first_hunk_end_marker = 1"), files) is not None
    assert G.ground(claim(file="long.py", quote="line 4\nline 28"), files) is None  # the last line of one hunk and the first of the next are not adjacent


def test_a_quote_that_is_not_in_the_file_is_dropped(files):
    assert G.ground(claim(quote="subprocess.run(cmd, shell=True)"), files) is None


def test_a_file_that_is_not_in_the_diff_is_dropped(files):
    assert G.ground(claim(file="other.py"), files) is None


def test_a_quote_from_a_different_file_is_not_reattributed():
    files = by_path(files_of(make_diff("a.py", None, ["secret_call(1)"]), make_diff("b.py", None, ["x = 2"])))
    assert G.ground(claim(file="b.py", quote="secret_call(1)"), files) is None


def test_a_quote_that_only_exists_on_a_removed_line_is_dropped(files):
    assert G.ground(claim(quote="data = open(path).read()"), files) is None


def test_a_quote_on_an_unchanged_context_line_is_kept_but_only_as_a_note(files):
    finding = G.ground(claim(quote="return data", severity="warn"), files)
    assert finding is not None and finding.severity is Severity.INFO and "unchanged" in finding.evidence


def test_a_changed_occurrence_wins_over_a_context_occurrence():
    files = by_path(edited_file("a.py", ["x = 1", "y = compute(2)", "z = 3"], ["x = 1", "y = compute(2)", "y = compute(2)", "z = 3"]))
    finding = G.ground(claim(file="a.py", quote="y = compute(2)"), files)
    assert finding is not None and finding.line == 3


@pytest.mark.parametrize("quote", ["", "}", "x", "   ", "y = 2", "a  b"])
def test_a_quote_too_short_to_locate_anything_is_dropped(files, quote):
    assert G.ground(claim(quote=quote), files) is None


@pytest.mark.parametrize("severity,expected", [("block", Severity.WARN), ("critical", Severity.INFO), ("warn", Severity.WARN), ("info", Severity.INFO), ("", Severity.INFO)])
def test_a_model_can_never_block(files, severity, expected):
    assert G.ground(claim(severity=severity), files).severity is expected


def test_the_quote_is_the_harness_text_and_secrets_in_it_are_redacted():
    token = fake_token("github")
    files = by_path(added_file("c.py", [f"TOKEN = '{token}'", "other = 1"]))
    finding = G.ground(claim(file="c.py", quote=f"TOKEN = '{token}'"), files)
    assert finding is not None and token not in repr(finding) and "REDACTED" in finding.quote


def test_the_message_is_data_bounded_stripped_of_control_characters_and_scrubbed():
    token = fake_token("openrouter")
    files = by_path(added_file("c.py", ["value = compute()"]))
    finding = G.ground(claim(file="c.py", quote="value = compute()", message="bad\x1b[31m red\x07 " + token + " " + "x" * 2000, suggestion="s" * 2000), files)
    assert finding is not None
    assert "\x1b" not in finding.message and "[31m" not in finding.message and "\x07" not in finding.message and token not in finding.message
    assert finding.message.startswith("bad red ")
    assert len(finding.message) <= G.MAX_MESSAGE_CHARS and len(finding.suggestion) <= G.MAX_SUGGESTION_CHARS


def test_a_cited_repo_rule_that_exists_is_used_and_an_invented_one_is_not():
    rules = Rules((Rule("no-eval", "no eval", Severity.WARN, ("**",), None, True),))
    files = by_path(added_file("c.py", ["value = eval(x)"]))
    cited = G.ground(claim(file="c.py", quote="value = eval(x)", rule="no-eval"), files, rules)
    invented = G.ground(claim(file="c.py", quote="value = eval(x)", rule="made-up"), files, rules)
    assert cited.rule == "rule:no-eval" and invented.rule == "model:security"


def test_two_lenses_flagging_the_same_code_are_one_finding_that_remembers_both():
    files = by_path(added_file("c.py", ["value = eval(x)"]))
    a = G.ground(claim(file="c.py", quote="value = eval(x)", lens="security", severity="info"), files)
    b = G.ground(claim(file="c.py", quote="value = eval(x)", lens="correctness", severity="warn", message="different words"), files)
    (merged,) = G.merge_duplicates([a, b])
    assert merged.severity is Severity.WARN and "security" in merged.evidence and "correctness" in merged.evidence


def test_different_code_is_not_merged():
    files = by_path(added_file("c.py", ["value = eval(x)", "other = exec(y)"]))
    a = G.ground(claim(file="c.py", quote="value = eval(x)"), files)
    b = G.ground(claim(file="c.py", quote="other = exec(y)"), files)
    assert len(G.merge_duplicates([a, b])) == 2
