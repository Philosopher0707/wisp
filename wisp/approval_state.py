"""Interactive approval state for per-session tool permissions.

Tracks user choices so they don't have to re-approve the same tool
type within a session.

Usage::
    state = ApprovalSessionState()
    assert state.should_ask("run_bash")  # First time
    state.allow_tool("run_bash")         # User pressed 'Y'
    assert not state.should_ask("run_bash")  # Already allowed
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import StrEnum


class SessionPolicy(StrEnum):
    """Session-wide approval behaviour."""

    AUTO = "auto"     # Approve everything  (equiv --perm full)
    PROMPT = "prompt" # Ask each time       (equiv --perm ask_all)
    BLOCK = "block"   # Deny everything     (equiv --perm read_only)


@dataclass
class ApprovalSessionState:
    """Mutable session-level approval tracking."""

    # Session-wide policy (user can press 'a' or 'd' at any prompt)
    session_policy: SessionPolicy = SessionPolicy.PROMPT

    # Tool names explicitly allowed this session (user pressed Y)
    allowed_tools: set[str] = field(default_factory=set)

    # Tool names explicitly denied this session (user pressed N)
    denied_tools: set[str] = field(default_factory=set)

    def should_ask(self, tool_name: str) -> bool:
        """Return True if we should prompt the user for this tool."""
        match self.session_policy:
            case SessionPolicy.AUTO:
                return False
            case SessionPolicy.BLOCK:
                return True
            case SessionPolicy.PROMPT:
                if tool_name in self.allowed_tools:
                    return False
                # Even in PROMPT, blocked tools are silently denied
                if tool_name in self.denied_tools:
                    return False
                return True

    def is_allowed(self, tool_name: str) -> bool:
        """Return True if the tool is allowed to run."""
        match self.session_policy:
            case SessionPolicy.AUTO:
                return True
            case SessionPolicy.BLOCK:
                return False
            case SessionPolicy.PROMPT:
                return tool_name in self.allowed_tools

    def allow_tool(self, tool_name: str) -> None:
        """User pressed Y (always allow this tool)."""
        self.allowed_tools.add(tool_name)
        self.denied_tools.discard(tool_name)

    def deny_tool(self, tool_name: str) -> None:
        """User pressed N (always deny this tool)."""
        self.denied_tools.add(tool_name)
        self.allowed_tools.discard(tool_name)

    def set_auto(self) -> None:
        """User pressed a (approve everything)."""
        self.session_policy = SessionPolicy.AUTO

    def set_block(self) -> None:
        """User pressed d (deny everything)."""
        self.session_policy = SessionPolicy.BLOCK

    def to_dict(self) -> dict:
        return asdict(self, dict_factory=lambda x: {
            "session_policy": self.session_policy.value,
            "allowed_tools": list(self.allowed_tools),
            "denied_tools": list(self.denied_tools),
        })

    @classmethod
    def from_dict(cls, data: dict) -> "ApprovalSessionState":
        return cls(
            session_policy=SessionPolicy(data.get("session_policy", "prompt")),
            allowed_tools=set(data.get("allowed_tools", [])),
            denied_tools=set(data.get("denied_tools", [])),
        )
