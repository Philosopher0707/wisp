"""The unified-diff parser: every number the review reports comes from here, so it is checked against real `git diff` output, not only hand-written text.

The property that matters is that a post-image line's `new_no` is the line's real number in the file after the change: a finding that points at the wrong
line is worse than none. It is asserted for every added and context line of every file in a real repository, through edits, additions, deletions, renames,
binary files, names with spaces, a missing final newline and CRLF.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wisp.review.diff import FileDiff, parse_unified_diff

SAMPLE = """\
diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 import os
-x = 1
+x = 2
+y = 3
 def f():
@@ -10,3 +11,3 @@ def g():
     a = 1
-    b = 2
+    b = 3
     return a
"""


def test_a_hand_written_diff_gives_the_new_file_line_numbers():
    (fd,) = parse_unified_diff(SAMPLE)
    assert fd.path == "app.py" and fd.status == "modified" and not fd.binary
    added = [(n, t) for n, t in fd.added_lines()]
    assert added == [(2, "x = 2"), (3, "y = 3"), (12, "    b = 3")]
    assert [(ln.old_no, ln.text) for h in fd.hunks for ln in h.lines if ln.kind == "-"] == [(2, "x = 1"), (11, "    b = 2")]
    assert fd.added == 3 and fd.removed == 2


def test_context_lines_are_part_of_the_post_image_with_their_numbers():
    (fd,) = parse_unified_diff(SAMPLE)
    post = {n: t for n, t in fd.post_image_lines()}
    assert post[1] == "import os" and post[4] == "def f():" and post[11] == "    a = 1" and post[13] == "    return a"


def test_a_new_file_a_deleted_file_and_a_rename():
    text = (
        "diff --git a/new.py b/new.py\nnew file mode 100644\nindex 0000000..3333333\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+a\n+b\n"
        "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\nindex 4444444..0000000\n--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
        "diff --git a/old.py b/moved.py\nsimilarity index 90%\nrename from old.py\nrename to moved.py\nindex 5555555..6666666 100644\n--- a/old.py\n+++ b/moved.py\n"
        "@@ -1 +1 @@\n-x\n+y\n"
    )
    added, deleted, renamed = parse_unified_diff(text)
    assert (added.path, added.status, added.added) == ("new.py", "added", 2)
    assert (deleted.path, deleted.status, deleted.removed) == ("gone.py", "deleted", 2)
    assert (renamed.path, renamed.old_path, renamed.status) == ("moved.py", "old.py", "renamed")


def test_a_binary_file_and_a_mode_only_change_have_no_hunks():
    text = (
        "diff --git a/img.png b/img.png\nindex 1..2 100644\nBinary files a/img.png and b/img.png differ\n"
        "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"
    )
    img, mode = parse_unified_diff(text)
    assert img.binary and img.hunks == [] and img.path == "img.png"
    assert not mode.binary and mode.hunks == [] and mode.path == "run.sh" and mode.status == "modified"


def test_a_line_that_looks_like_a_header_inside_a_hunk_is_content_not_a_new_file():
    text = (
        "diff --git a/doc.md b/doc.md\n--- a/doc.md\n+++ b/doc.md\n@@ -1,2 +1,4 @@\n context\n+diff --git a/fake b/fake\n+--- a/fake\n tail\n"
        "diff --git a/real.py b/real.py\n--- a/real.py\n+++ b/real.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    doc, real = parse_unified_diff(text)
    assert [fd.path for fd in (doc, real)] == ["doc.md", "real.py"]
    assert [t for _n, t in doc.added_lines()] == ["diff --git a/fake b/fake", "--- a/fake"]


def test_no_newline_at_end_of_file_marker_is_ignored():
    text = "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n"
    (fd,) = parse_unified_diff(text)
    assert [t for _n, t in fd.added_lines()] == ["new"] and fd.removed == 1


def test_empty_and_garbage_input_do_not_raise_and_report_what_was_not_understood():
    assert parse_unified_diff("") == []
    files = parse_unified_diff("this is not a diff\nat all\n")
    assert files == []


def test_unparsed_lines_are_counted_so_the_report_cannot_call_a_partial_parse_complete():
    from wisp.review.diff import parse_with_residue

    text = SAMPLE + "garbage after the last hunk\n"
    files, residue = parse_with_residue(text)
    assert len(files) == 1 and residue == 1
    _, clean = parse_with_residue(SAMPLE)
    assert clean == 0


def test_a_truncated_hunk_is_reported_not_trusted():
    from wisp.review.diff import parse_with_residue

    text = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,5 +1,5 @@\n a\n-b\n+c\n"
    files, residue = parse_with_residue(text)
    assert residue > 0 and files[0].truncated


# ── against real git ─────────────────────────────────────────────────────────


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("".join(f"line {i}\n" for i in range(1, 41)))
    (tmp_path / "my file.txt").write_text("one\ntwo\n")
    (tmp_path / "crlf.txt").write_bytes(b"a\r\nb\r\nc\r\n")
    (tmp_path / "nonl.txt").write_bytes(b"x\ny")
    (tmp_path / "old_name.py").write_text("keep\n" * 30)
    (tmp_path / "doomed.py").write_text("bye\n")
    (tmp_path / "img.bin").write_bytes(bytes(range(256)))
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def change_everything(repo: Path) -> None:
    lines = (repo / "a.py").read_text().splitlines()
    lines[2] = "line 3 changed"
    del lines[10:12]
    lines.insert(25, "inserted a")
    lines.insert(26, "inserted b")
    lines[-1] = "last changed"
    (repo / "a.py").write_text("\n".join(lines) + "\n")
    (repo / "my file.txt").write_text("one\ntwo\nthree\n")
    (repo / "crlf.txt").write_bytes(b"a\r\nB\r\nc\r\nd\r\n")
    (repo / "nonl.txt").write_bytes(b"x\ny\nz")
    git(repo, "mv", "old_name.py", "new_name.py")
    (repo / "new_name.py").write_text("keep\n" * 14 + "edited\n" + "keep\n" * 15)
    (repo / "doomed.py").unlink()
    (repo / "added.py").write_text("n1\nn2\nn3\n")
    (repo / "img.bin").write_bytes(bytes(reversed(range(256))))
    git(repo, "add", "-A")


def test_every_post_image_line_number_is_the_real_line_in_the_file(repo):
    change_everything(repo)
    text = git(repo, "diff", "--cached", "-M")
    files = parse_unified_diff(text)
    by_path = {fd.path: fd for fd in files}
    assert {"a.py", "my file.txt", "crlf.txt", "nonl.txt", "new_name.py", "doomed.py", "added.py", "img.bin"} <= set(by_path)
    checked = 0
    for fd in files:
        if fd.binary or fd.status == "deleted":
            continue
        real = (repo / fd.path).read_bytes().decode().replace("\r\n", "\n").split("\n")
        for number, line_text in fd.post_image_lines():
            assert real[number - 1].rstrip("\r") == line_text.rstrip("\r"), (fd.path, number, line_text, real[number - 1])
            checked += 1
    assert checked > 40


def test_statuses_and_flags_from_real_git(repo):
    change_everything(repo)
    by_path = {fd.path: fd for fd in parse_unified_diff(git(repo, "diff", "--cached", "-M"))}
    assert by_path["doomed.py"].status == "deleted" and by_path["added.py"].status == "added"
    assert by_path["new_name.py"].status == "renamed" and by_path["new_name.py"].old_path == "old_name.py"
    assert by_path["img.bin"].binary and by_path["a.py"].status == "modified"


def test_the_added_and_removed_counts_match_git_numstat(repo):
    change_everything(repo)
    files = {fd.path: fd for fd in parse_unified_diff(git(repo, "diff", "--cached", "-M"))}
    for row in git(repo, "diff", "--cached", "-M", "--numstat").splitlines():
        a, r, name = row.split("\t")
        if a == "-":
            continue
        name = name.split(" => ")[-1].strip("{}") if " => " in name else name
        path = next(p for p in files if p == name or p.endswith(name))
        assert (files[path].added, files[path].removed) == (int(a), int(r)), path


def test_text_before_the_first_file_header_is_counted_not_ignored():
    from wisp.review.diff import parse_with_residue

    files, residue = parse_with_residue("commit abc123\nAuthor: someone\n\n" + SAMPLE)
    assert len(files) == 1 and residue == 3


def test_the_parser_accepts_what_the_source_module_will_feed_it():
    assert isinstance(parse_unified_diff(SAMPLE)[0], FileDiff)


def test_an_empty_new_file_and_an_empty_deleted_file_have_a_status_but_no_hunks():
    text = (
        "diff --git a/empty.txt b/empty.txt\nnew file mode 100644\nindex 0000000..e69de29\n"
        "diff --git a/old_empty.txt b/old_empty.txt\ndeleted file mode 100644\nindex e69de29..0000000\n"
    )
    added, deleted = parse_unified_diff(text)
    assert (added.path, added.status, added.hunks) == ("empty.txt", "added", [])
    assert (deleted.path, deleted.status, deleted.hunks) == ("old_empty.txt", "deleted", [])


def test_a_quoted_path_in_a_header_with_no_body_is_read():
    text = 'diff --git "a/with\\ttab.bin" "b/with\\ttab.bin"\nBinary files "a/with\\ttab.bin" and "b/with\\ttab.bin" differ\n'
    (fd,) = parse_unified_diff(text)
    assert fd.path == "with\\ttab.bin" and fd.binary


def test_a_header_line_nobody_knows_is_counted_not_silently_accepted():
    from wisp.review.diff import parse_with_residue

    files, residue = parse_with_residue("diff --git a/a.py b/a.py\nsome new git header we have never seen\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert len(files) == 1 and residue == 1
