import gleam/http.{Get, Post}
import gleam/list
import gleam/result
import wisp.{type Request, type Response}
import diff_demo/app/web
import diff_demo/diff_html

pub fn handle_request(req: Request) -> Response {
  use req <- web.middleware(req)

  case req.method, wisp.path_segments(req) {
    Get, [] -> show_form()
    Post, ["diff"] -> handle_diff_submission(req)
    _, _ -> wisp.not_found()
  }
}

fn show_form() -> Response {
  let html =
    "<!DOCTYPE html>
<html>
<head>
  <meta charset=\"UTF-8\">
  <title>Syntax-Highlighted Diff</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; background: #0d1117; color: #c9d1d9; }
    h1 { color: #58a6ff; }
    label { display: block; margin-top: 1rem; font-weight: 600; }
    textarea { width: 100%; height: 250px; font-family: ui-monospace, monospace; font-size: 14px; background: #161b22; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; padding: 0.75rem; }
    select, button { font-size: 1rem; padding: 0.5rem 1rem; border-radius: 6px; border: 1px solid #30363d; background: #21262d; color: #c9d1d9; cursor: pointer; }
    button { background: #238636; color: white; border-color: #238636; font-weight: 600; }
    button:hover { background: #2ea043; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  </style>
</head>
<body>
  <h1>🧚 Syntax-Highlighted Diff for Edits</h1>
  <form method=\"post\" action=\"/diff\">
    <label>Language</label>
    <select name=\"lang\">
      <option value=\"gleam\">Gleam</option>
      <option value=\"python\">Python</option>
      <option value=\"javascript\">JavaScript</option>
      <option value=\"rust\">Rust</option>
    </select>
    <div class=\"row\">
      <div>
        <label>Original code</label>
        <textarea name=\"old\" placeholder=\"Paste original code here...\"></textarea>
      </div>
      <div>
        <label>Modified code</label>
        <textarea name=\"new\" placeholder=\"Paste modified code here...\"></textarea>
      </div>
    </div>
    <p><button type=\"submit\">Generate Diff</button></p>
  </form>
</body>
</html>"

  wisp.ok()
  |> wisp.html_body(html)
}

fn handle_diff_submission(req: Request) -> Response {
  use formdata <- wisp.require_form(req)

  let old = result.unwrap(list.key_find(formdata.values, "old"), "")
  let new = result.unwrap(list.key_find(formdata.values, "new"), "")
  let lang = result.unwrap(list.key_find(formdata.values, "lang"), "gleam")

  let body = diff_html.render_diff(old, new, lang)

  wisp.ok()
  |> wisp.html_body(body)
}
