# Syntax-Highlighted Diff for Wisp

A minimal [Wisp](https://github.com/gleam-wisp/wisp) (Gleam web framework) application that renders **syntax-highlighted diffs** for code edits.

## Features

- 🧚 Built with **Wisp** — the practical Gleam web framework
- 🎨 **Syntax highlighting** powered by [`smalto`](https://hexdocs.pm/smalto) (36+ languages)
- ➕➖ **Line-level diffs** — shows added/removed/changed lines
- 🌙 **GitHub-dark styling** out of the box
- 🖥️ **Side-by-side form** — paste original and modified code, pick a language, and generate

## Quick Start

### 1. Install dependencies

```bash
gleam deps download
```

### 2. Run the server

```bash
gleam run
```

The server starts on **http://localhost:8000**.

### 3. Generate a diff

1. Open http://localhost:8000  
2. Select a language (Gleam, Python, JavaScript, or Rust)  
3. Paste your **original** and **modified** code  
4. Click **Generate Diff**

## Project Structure

```
├── gleam.toml
├── src/
│   └── diff_demo/
│       ├── app.gleam          # Entry point — starts Mist server
│       ├── app/
│       │   ├── router.gleam   # Routes: GET / (form), POST /diff (result)
│       │   └── web.gleam      # Middleware stack
│       ├── diff.gleam         # Pure line-diff algorithm
│       └── diff_html.gleam    # Renders highlighted diff as HTML
```

## How It Works

### Diff Algorithm (`src/diff_demo/diff.gleam`)

A pure Gleam line-diff that classifies each line as:

- `Equal(String)` — unchanged
- `Added(String)` — present only in the new version
- `Removed(String)` — present only in the old version

### Syntax Highlighting (`src/diff_demo/diff_html.gleam`)

Uses `smalto.to_html/2` to tokenize each line into spans with CSS classes (`.smalto-keyword`, `.smalto-string`, etc.). The diff wrapper adds background colors:

- **Green** (`diff-added`) for insertions  
- **Red** (`diff-removed`) for deletions  
- **Transparent** (`diff-equal`) for unchanged lines

## Customizing

### Add more languages

Import the language module in `diff_html.gleam`:

```gleam
import smalto/languages/go
```

Then add a branch in `get_grammar`:

```gleam
"go" -> go.grammar()
```

### Change the theme

Edit the CSS string in `diff_html.gleam` or swap `smalto` for `smalto_lustre` + `smalto_lustre_themes` if you want pre-built Prism.js themes.

## Dependencies

| Package | Purpose |
|---------|---------|
| `wisp` | Web framework |
| `wisp_mist` | Wisp adapter for the Mist HTTP server |
| `mist` | HTTP server |
| `smalto` | Syntax highlighter (36+ languages) |
| `gleam_stdlib` | Standard library |

## License

Apache-2.0
