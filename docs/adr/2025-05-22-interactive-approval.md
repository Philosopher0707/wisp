# ADR: Interactive Tool Approval System

## Status: Proposed

## Context

The current security model has 4 coarse permission modes:
- **FULL**: All tools auto-approved
- **AUTO_EDIT**: Bash blocked, file writes auto-approved
- **ASK_ALL**: Requires approval for all mutating tools
- **READ_ONLY**: Only safe reads allowed

Users need finer-grained control. Coding agents need bash, but users want to review individual commands before they run. The `ASK_ALL` mode exists but the approval flow is broken — CLITransport just returns `True`.

## Decision

Replace `PermissionMode` with a 2-axis system:

### Axis 1: Default Behavior (`policy`)
| Policy | Description |
|--------|-------------|
| `prompt` | Ask for every tool (interactive) |
| `auto` | Auto-approve everything |
| `block` | Deny everything (unless explicitly allowed) |

### Axis 2: Permission Matrix

Users answer each approval request with one of:
| Response | Meaning |
|----------|---------|
| `y` | Yes — run this tool once |
| `Y` | Yes — always allow this tool this session |
| `a` | Yes — allow all tools this session (switch to auto) |
| `n` | No — deny this tool once |
| `N` | No — always deny this tool this session |
| `d` | No — deny all tools this session (switch to block) |
| `c` | Cancel — stop the turn |

### State Tracking

Per-session state:
```python
@dataclass
class ApprovalSessionState:
    # Tools explicitly allowed for this session (user said Y)
    allowed_tools: set[str] = field(default_factory=set)
    # Tools explicitly denied for this session (user said N)
    denied_tools: set[str] = field(default_factory=set)
    # Session-wide policy (a/d override)
    session_policy: Literal["prompt", "auto", "block"] = "prompt"
    # User answers are sticky until session ends
    defaults_set: bool = False
```

### CLI Prompt Design

```
⚠️  run_bash wants to execute:
   $ ls -la

   [y] run once  [Y] always this tool  [a] approve all  [n] skip  [N] always skip  [d] deny all  [c] cancel

> y
```

### Backward Compatibility

- `--perm full` → `policy=auto`
- `--perm auto_edit` → `policy=prompt` for bash, auto for file edits
- `--perm ask_all` → `policy=prompt`
- `--perm read_only` → `policy=block` + allow reads

### Implementation Plan

1. **Phase A** (Now): Fix `CLITransport.approve()` to actually prompt
2. **Phase B** (Next): Add `ApprovalSessionState` to session dict
3. **Phase C** (Later): Persist user choices to `~/.config/wisp/approval.json`

## State Machine

```
[prompt] ──Y──→ (add tool to allowed, stays prompt)
         ──y──→ (run once, stays prompt)
         ──a──→ (switch to auto)
         ──n──→ (skip once, stays prompt)
         ──N──→ (add tool to denied, stays prompt)
         ──d──→ (switch to block)
         ──c──→ (cancel turn)

[auto] ──any──→ (run, stays auto)

[block] ──y/Y──→ (deny, stays block)
        ──a───→ (switch to auto)
```

## Risks

- Session loss = approval memory loss (Phase C fixes)
- TUI/WebSocket also need to support this flow
- Default should still be `auto` for headless/server mode
