import gleam/list
import gleam/string
import diff_demo/diff.{type DiffLine, Equal, Added, Removed}
import smalto
import smalto/languages/gleam
import smalto/languages/python
import smalto/languages/javascript
import smalto/languages/rust

fn get_grammar(lang: String) {
  case lang {
    "python" -> python.grammar()
    "javascript" | "js" -> javascript.grammar()
    "rust" -> rust.grammar()
    _ -> gleam.grammar()
  }
}

pub fn render_diff(old: String, new: String, language: String) -> String {
  let diff_lines = diff.diff_text(old, new)
  let grammar = get_grammar(language)

  let lines_html = list.map(diff_lines, fn(line) {
    let #(class, content, marker) = case line {
      Equal(c) -> #("diff-equal", c, " ")
      Added(c) -> #("diff-added", c, "+")
      Removed(c) -> #("diff-removed", c, "-")
    }

    let highlighted = smalto.to_html(content, grammar)

    "<div class=\"diff-line "
      <> class
      <> "\"><span class=\"diff-marker\">"
      <> marker
      <> "</span><code>"
      <> highlighted
      <> "</code></div>"
  })

  let css =
    "body { margin: 0; background: #0d1117; color: #c9d1d9; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
     .diff-container { padding: 1rem; }
     .diff-line { display: flex; align-items: baseline; min-height: 1.6em; white-space: pre-wrap; word-break: break-word; }
     .diff-marker { width: 2ch; text-align: center; user-select: none; flex-shrink: 0; margin-right: 0.5ch; }
     .diff-equal { }
     .diff-added { background: rgba(46, 160, 67, 0.15); }
     .diff-added .diff-marker { color: #3fb950; font-weight: bold; }
     .diff-removed { background: rgba(248, 81, 73, 0.15); }
     .diff-removed .diff-marker { color: #f85149; font-weight: bold; }
     code { font-family: inherit; }
     .smalto-keyword { color: #ff7b72; }
     .smalto-string { color: #a5d6ff; }
     .smalto-number { color: #79c0ff; }
     .smalto-comment { color: #8b949e; font-style: italic; }
     .smalto-function { color: #d2a8ff; }
     .smalto-operator { color: #ff7b72; }
     .smalto-type { color: #ffa657; }
     .smalto-module { color: #ffa657; }
     .smalto-tag { color: #7ee787; }
     .smalto-attribute { color: #79c0ff; }
     .smalto-selector { color: #d2a8ff; }
     .smalto-property { color: #79c0ff; }"

  "<!DOCTYPE html>
<html>
<head>
  <meta charset=\"UTF-8\">
  <title>Diff Result</title>
  <style>"
    <> css
    <> "</style>
</head>
<body>
  <div class=\"diff-container\">"
    <> string.join(lines_html, "\n")
    <> "</div>
</body>
</html>"
}
