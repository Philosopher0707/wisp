import gleeunit
import gleeunit/should
import diff_demo/diff

pub fn main() {
  gleeunit.main()
}

pub fn diff_equal_test() {
  diff.diff_text("a\nb", "a\nb")
  |> should.equal([diff.Equal("a"), diff.Equal("b")])
}

pub fn diff_added_test() {
  diff.diff_text("a", "a\nb")
  |> should.equal([diff.Equal("a"), diff.Added("b")])
}

pub fn diff_removed_test() {
  diff.diff_text("a\nb", "a")
  |> should.equal([diff.Equal("a"), diff.Removed("b")])
}

pub fn diff_changed_test() {
  diff.diff_text("a\nb", "a\nc")
  |> should.equal([diff.Equal("a"), diff.Removed("b"), diff.Added("c")])
}
