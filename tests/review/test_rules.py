"""Repo rules: a TOML file the owner writes. A rule with a `forbid` pattern is checked deterministically; its text also goes to the model. A bad rules file is an error, never silently ignored."""

from __future__ import annotations

import pytest

from tests.review.helpers import added_file, edited_file, fake_token, files_of, make_diff
from wisp.review import rules as R
from wisp.review.types import Severity

VALID = """
[[rule]]
id = "no-print"
text = "Library code logs through logging, never print()."
severity = "warn"
paths = ["wisp/**/*.py"]
forbid = '^\\s*print\\('

[[rule]]
id = "explain-sleeps"
text = "Every time.sleep needs a comment saying why."
model = true

[[rule]]
id = "no-todo-in-prod"
text = "No TODO markers in shipped code."
severity = "block"
forbid = 'TODO'
paths = ["*.py"]
model = false
"""


def load(tmp_path, text):
    path = tmp_path / "review-rules.toml"
    path.write_text(text)
    return R.load_rules(path)


def test_a_valid_file_loads_with_defaults(tmp_path):
    rules = load(tmp_path, VALID)
    assert [r.id for r in rules.rules] == ["no-print", "explain-sleeps", "no-todo-in-prod"]
    second = rules.rules[1]
    assert second.severity is Severity.WARN and second.paths == ("**",) and second.forbid is None and second.model is True


def test_a_missing_file_means_no_rules_not_an_error(tmp_path):
    assert R.load_rules(tmp_path / "nope.toml").rules == ()


def test_forbid_rules_find_only_added_lines_in_matching_paths(tmp_path):
    rules = load(tmp_path, VALID)
    files = files_of(
        make_diff("wisp/a.py", ["x = 1"], ["x = 1", "print('hi')"]),
        make_diff("wisp/sub/b.py", None, ["print(1)"]),
        make_diff("scripts/c.py", None, ["print(1)"]),  # outside wisp/**
        make_diff("wisp/d.py", ["print(1)", "y = 2"], ["y = 2"]),  # removed, not added
    )
    found = R.check_rules(files, rules)
    assert sorted((f.file, f.line, f.rule) for f in found) == [("wisp/a.py", 2, "rule:no-print"), ("wisp/sub/b.py", 1, "rule:no-print")]


def test_a_rules_own_severity_can_block_because_a_pattern_match_is_established_evidence(tmp_path):
    rules = load(tmp_path, VALID)
    (finding,) = R.check_rules(added_file("anything/app.py", ["x = 1  # TODO later"]), rules)
    assert (finding.rule, finding.severity, finding.line) == ("rule:no-todo-in-prod", Severity.BLOCK, 1)
    assert finding.message == "No TODO markers in shipped code."


def test_a_pattern_without_a_slash_matches_the_file_name_anywhere(tmp_path):
    rules = load(tmp_path, VALID)
    assert R.check_rules(added_file("deep/er/mod.py", ["# TODO"]), rules)
    assert not R.check_rules(added_file("deep/er/mod.txt", ["# TODO"]), rules)


def test_single_star_does_not_cross_directories_and_double_star_does(tmp_path):
    text = '[[rule]]\nid = "a"\ntext = "t"\nforbid = "x"\npaths = ["tests/*.py"]\n'
    rules = load(tmp_path, text)
    assert R.check_rules(added_file("tests/a.py", ["x"]), rules)
    assert not R.check_rules(added_file("tests/sub/a.py", ["x"]), rules)
    deep = load(tmp_path, text.replace("tests/*.py", "tests/**/*.py"))
    assert R.check_rules(added_file("tests/sub/a.py", ["x"]), deep)
    assert R.check_rules(added_file("tests/a.py", ["x"]), deep)


def test_the_quote_is_scrubbed(tmp_path):
    token = fake_token("github")
    rules = load(tmp_path, '[[rule]]\nid = "r"\ntext = "t"\nforbid = "token"\n')
    (finding,) = R.check_rules(added_file("a.txt", [f"token = '{token}'"]), rules)
    assert token not in repr(finding)


def test_rules_without_a_forbid_pattern_are_for_the_model_only(tmp_path):
    rules = load(tmp_path, VALID)
    assert R.check_rules(added_file("wisp/a.py", ["import time", "time.sleep(1)"]), rules) == []
    text = rules.model_text()
    assert "explain-sleeps" in text and "no-print" in text and "no-todo-in-prod" not in text  # model = false is left out


@pytest.mark.parametrize("text,needle", [
    ('[[rule]]\nid = "x"\ntext = "t"\nbogus = 1\n', "unknown key 'bogus'"),
    ('[[rule]]\nid = "Bad Id"\ntext = "t"\n', "id"),
    ('[[rule]]\nid = "a"\ntext = "t"\n[[rule]]\nid = "a"\ntext = "u"\n', "duplicate id 'a'"),
    ('[[rule]]\nid = "a"\ntext = ""\n', "text"),
    ('[[rule]]\nid = "a"\ntext = "t"\nseverity = "fatal"\n', "severity"),
    ('[[rule]]\nid = "a"\ntext = "t"\nforbid = "("\n', "forbid"),
    ('[[rule]]\nid = "a"\ntext = "t"\nforbid = "' + "x" * 600 + '"\n', "forbid"),
    ('[[rule]]\nid = "a"\ntext = "t"\npaths = "wisp/**"\n', "paths"),
    ('[[rule]]\nid = "a"\ntext = "t"\nmodel = "yes"\n', "model"),
    ('stray = 1\n[[rule]]\nid = "a"\ntext = "t"\n', "unknown top-level key 'stray'"),
    ("this is = not toml [", "not valid TOML"),
])
def test_every_kind_of_bad_file_is_an_error_naming_the_problem(tmp_path, text, needle):
    with pytest.raises(R.RulesError) as caught:
        load(tmp_path, text)
    assert needle in str(caught.value)


def test_all_problems_are_reported_together(tmp_path):
    with pytest.raises(R.RulesError) as caught:
        load(tmp_path, '[[rule]]\nid = "A"\ntext = ""\nseverity = "x"\n')
    message = str(caught.value)
    assert message.count("\n") >= 2 and "id" in message and "text" in message and "severity" in message


def test_the_default_location_is_inside_the_repo(tmp_path):
    (tmp_path / ".wisp").mkdir()
    (tmp_path / ".wisp" / "review-rules.toml").write_text(VALID)
    assert len(R.load_rules(R.default_path(str(tmp_path))).rules) == 3


def test_an_edited_file_with_the_forbidden_text_only_in_context_is_clean(tmp_path):
    rules = load(tmp_path, VALID)
    files = edited_file("wisp/a.py", ["print('old')", "a", "b"], ["print('old')", "a", "b", "c"])
    assert R.check_rules(files, rules) == []
