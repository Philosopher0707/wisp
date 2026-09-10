"""12.5C: one containment primitive — equivalence + adversarial corpus.

THREAT: path traversal / symlink / NUL / prefix tricks escape roots.
EXPECTED: resolve_contained proves containment or rejects; all three
callers share the verdict.
"""

from __future__ import annotations

import os

import pytest

from wisp.pathsec import resolve_contained


def _ws(tmp_path, files=("a.py", "sub/b.py")):
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n")
    try:
        os.symlink(str(tmp_path / "a.py"), str(tmp_path / "link.py"))
        os.symlink("/nonexistent-target-xyz", str(tmp_path / "broken.py"))
        os.symlink("/etc", str(tmp_path / "outlink"))
    except OSError:
        pass
    return str(tmp_path)


class TestEquivalence:
    def test_callers_agree(self, tmp_path):
        from wisp.tools._utils import _resolve_path
        from wisp.graph.artifacts import _contain as art_contain
        from wisp.workspace import _contain as ws_contain
        ws = _ws(tmp_path)
        for rel in ("a.py", "sub/b.py", "."):
            a = str(_resolve_path(rel, ws))
            b = art_contain(ws, rel)
            c = ws_contain(ws, rel)
            assert os.path.realpath(a) == os.path.realpath(b) == os.path.realpath(c)
        for evil in ("../e.py", "/abs.py", "outlink", "outlink/x.py"):
            for fn in (lambda p: _resolve_path(p, ws), lambda p: art_contain(ws, p),
                       lambda p: ws_contain(ws, p)):
                with pytest.raises(Exception):
                    fn(evil)
        # Realpath semantics agree: in-workspace symlinks resolve inside.
        if os.path.islink(os.path.join(ws, "link.py")):
            for fn in (lambda p: _resolve_path(p, ws), lambda p: art_contain(ws, p),
                       lambda p: ws_contain(ws, p)):
                fn("link.py")


class TestCorpus:
    @pytest.mark.parametrize("evil", [
        "..", "../e.py", "a/../../e.py", "sub/../../../e.py",
        "/etc/passwd", "/tmp/x", "a.py\x00", "\x00", "a\x1bb.py",
        "outlink", "outlink/x.py",
    ])
    def test_rejected(self, tmp_path, evil):
        ws = _ws(tmp_path)
        with pytest.raises((ValueError, OSError)):
            resolve_contained(ws, evil)

    def test_empty_means_root(self, tmp_path):
        ws = _ws(tmp_path)
        assert resolve_contained(ws, "") == os.path.realpath(ws)

    def test_sibling_prefix(self, tmp_path):
        base = str(tmp_path)
        sib = base + "2"
        os.makedirs(sib, exist_ok=True)
        with pytest.raises(ValueError):
            resolve_contained(base, os.path.join(sib, "x.py"))

    def test_symlink_escape(self, tmp_path):
        ws = _ws(tmp_path)
        if not os.path.islink(os.path.join(ws, "outlink")):
            pytest.skip("no symlinks")
        with pytest.raises(ValueError):
            resolve_contained(ws, "outlink")
        with pytest.raises(ValueError):
            resolve_contained(ws, "outlink/x.py")
        with pytest.raises(ValueError):
            resolve_contained(ws, "broken.py")

    def test_root_itself_allowed(self, tmp_path):
        assert resolve_contained(str(tmp_path), ".") == os.path.realpath(str(tmp_path))

    def test_nonexistent_inside_allowed(self, tmp_path):
        out = resolve_contained(str(tmp_path), "new/sub/file.py")
        assert out.endswith(os.path.join("new", "sub", "file.py"))

    def test_absolute_inside_allowed_only_with_flag(self, tmp_path):
        target = os.path.join(str(tmp_path), "a.py")
        assert resolve_contained(str(tmp_path), target) == os.path.realpath(target)
        with pytest.raises(ValueError):
            resolve_contained(str(tmp_path), target, allow_absolute=False)

    def test_non_text_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            resolve_contained(str(tmp_path), None)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            resolve_contained(None, "a.py")  # type: ignore[arg-type]

    def test_fuzz_never_escapes(self, tmp_path):
        import random
        import string
        rng = random.Random(12500)
        ws = _ws(tmp_path)
        real = os.path.realpath(ws) + os.sep
        alphabet = string.printable + "../\\\x00é→"
        for _ in range(300):
            cand = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
            try:
                out = resolve_contained(ws, cand)
            except (ValueError, OSError):
                continue
            assert out == os.path.realpath(ws) or out.startswith(real), cand
