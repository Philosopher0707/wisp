import gleam/list
import gleam/string

pub type DiffLine {
  Equal(String)
  Added(String)
  Removed(String)
}

/// Compute a line-based diff between two strings.
pub fn diff_text(old: String, new: String) -> List(DiffLine) {
  let old_lines = string.split(old, "\n")
  let new_lines = string.split(new, "\n")
  diff_lists(old_lines, new_lines)
}

fn diff_lists(old: List(String), new: List(String)) -> List(DiffLine) {
  case old, new {
    [], [] -> []
    [], [n, ..nrest] -> [Added(n), ..diff_lists([], nrest)]
    [o, ..orest], [] -> [Removed(o), ..diff_lists(orest, [])]
    [o, ..orest], [n, ..nrest] if o == n -> {
      [Equal(o), ..diff_lists(orest, nrest)]
    }
    [o, ..orest], [n, ..nrest] -> {
      let o_in_new = list.contains(new, o)
      let n_in_old = list.contains(old, n)
      case o_in_new, n_in_old {
        True, True -> {
          // Both lines exist somewhere in the other side.
          // Emit a removal + addition and continue greedily.
          [Removed(o), Added(n), ..diff_lists(orest, nrest)]
        }
        True, False -> {
          // `o` appears later in new, so `n` is a fresh addition.
          [Added(n), ..diff_lists(old, nrest)]
        }
        False, True -> {
          // `n` appears later in old, so `o` is a removal.
          [Removed(o), ..diff_lists(orest, new)]
        }
        False, False -> {
          // Neither line appears on the other side — treat as a change.
          [Removed(o), Added(n), ..diff_lists(orest, nrest)]
        }
      }
    }
  }
}
