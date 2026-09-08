# User Hooks

Wisp runs your scripts around tool calls. Block dangerous commands, warn on
risky ones, observe results, or rewrite arguments — without touching wisp itself.

## Locations

| Scope | Directory |
|---|---|
| Project | `<workspace>/.wisp/hooks/*.json` |
| Global | `~/.config/wisp/hooks/*.json` |

Project hooks reload automatically; global hooks load at startup.

## File format

One JSON object per file:

```json
{
  "name": "no-rm-rf",
  "event": "pre_tool_use",
  "matcher": "run_bash",
  "command": "grep -q 'rm -rf /' <<< \"$WISP_TOOL_ARGS\" && exit 2 || exit 0",
  "timeout_seconds": 5,
  "enabled": true
}
```

| Field | Meaning |
|---|---|
| `event` | `pre_tool_use`, `pre_bash`, `pre_file_write`, `post_tool_use`, `post_bash` |
| `matcher` | Regex matched against the tool name (empty = all tools) |
| `command` | Shell command to run (see contract below) |
| `timeout_seconds` | Kill + block after N seconds (default 5) |
| `enabled` | `false` skips without deleting the file |

## Decision contract (exit codes)

| Exit | Decision |
|---|---|
| `0` | Allow |
| `1` | Warn (tool still runs; reason shown) |
| anything else | **Block** (tool never runs; stderr shown as the reason) |

Timeouts and hook crashes block (timeouts) or warn (crashes) — fail-closed
where it matters, never silently.

## Environment (strict allow-list)

Hooks never see your process environment. They get:

- `WISP_EVENT`, `WISP_TOOL_NAME`, `WISP_WORKSPACE`, `WISP_SESSION_ID`
- `WISP_TOOL_ARGS` — tool arguments as JSON (capped; large blobs truncated)
- `WISP_RESULT` — post-hooks only, the tool result (first 4000 chars)

`{tool_name}`, `{event}`, `{workspace}`, `{session_id}` placeholders in
`command` are substituted shell-quoted.

## Rewriting arguments

Print a JSON object with a `tool_args` key as the **last stdout line** —
the tool runs with your arguments instead:

```bash
# force all bash through a wrapper, keep original as comment
echo "{\"tool_args\": {\"command\": \"$WRAPPED\"}}"
exit 0
```

## Inspecting

`/hooks` lists loaded hooks with events and matchers. `wisp skills`-style
discovery is automatic — drop a file, use it.

## Example: block git pushes to main

```json
{
  "name": "no-push-main",
  "event": "pre_tool_use",
  "matcher": "git_push|run_bash",
  "command": "echo \"$WISP_TOOL_ARGS\" | grep -q 'main' && { echo 'pushing to main is blocked'; exit 2; } || exit 0"
}
```
