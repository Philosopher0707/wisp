"""Extractive summarization for Wisp sessions — no ML model required.

Uses lightweight heuristics (position scoring, keyword patterns) to distill
a conversation into a summary, key decisions, user preferences, open tasks,
and files touched.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Patterns ─────────────────────────────────────────────────────────

_DECISION_PATTERNS = [
    r"decided to\b",
    r"decided on\b",
    r"we decided\b",
    r"let's use\b",
    r"going with\b",
    r"\bchose\b",
    r"will use\b",
    r"settled on\b",
    r"opted for\b",
    r"going to use\b",
    r"selected\b",
    r"picked\b",
]

_PREFERENCE_PATTERNS = [
    r"i prefer\b",
    r"i like\b",
    r"i want\b",
    r"i need\b",
    r"i don't want\b",
    r"i dislike\b",
    r"please use\b",
    r"always\b",
    r"never\b",
    r"make sure\b",
    r"ensure\b",
]

_TASK_PATTERNS = [
    r"\bTODO\b",
    r"\bFIXME\b",
    r"\bHACK\b",
    r"next time\b",
    r"\blater\b",
    r"still need to\b",
    r"\bpending\b",
    r"not yet\b",
    r"up next\b",
    r"\bfuture\b",
    r"plan to\b",
    r"need to\b",
    r"should\b",
    r"will implement\b",
    r"will add\b",
    r"will fix\b",
]

_ACTION_VERBS = [
    "implemented", "added", "created", "fixed", "refactored",
    "built", "wrote", "updated", "modified", "changed",
    "removed", "deleted", "merged", "deployed", "released",
    "optimized", "improved", "enhanced", "completed", "finished",
]

_FILE_PATTERN = re.compile(
    r"[\w\-./]+\.(py|js|ts|jsx|tsx|rs|go|java|kt|swift|cpp|c|h|hpp|"
    r"rb|php|scala|clj|ex|exs|elm|hs|lua|md|json|yaml|yml|toml|ini|cfg|"
    r"dockerfile|sh|bash|zsh|fish|ps1|sql|graphql|proto|thrift)"
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


# ── Data model ───────────────────────────────────────────────────────

@dataclass
class SessionSummary:
    """Structured summary of a single Wisp session."""

    session_id: str
    timestamp: str
    workspace: str
    summary: str = ""
    key_decisions: list[str] = field(default_factory=list)
    user_preferences: list[str] = field(default_factory=list)
    open_tasks: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "workspace": self.workspace,
            "summary": self.summary,
            "key_decisions": self.key_decisions,
            "user_preferences": self.user_preferences,
            "open_tasks": self.open_tasks,
            "files_touched": self.files_touched,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionSummary:
        return cls(
            session_id=data.get("session_id", ""),
            timestamp=data.get("timestamp", ""),
            workspace=data.get("workspace", ""),
            summary=data.get("summary", ""),
            key_decisions=data.get("key_decisions", []),
            user_preferences=data.get("user_preferences", []),
            open_tasks=data.get("open_tasks", []),
            files_touched=data.get("files_touched", []),
        )


# ── Summarizer ───────────────────────────────────────────────────────

class ExtractiveSummarizer:
    """Summarize a conversation using heuristics — no ML model required."""

    def summarize(
        self,
        messages: list[dict],
        session_id: str,
        workspace: str,
    ) -> Optional[SessionSummary]:
        """Generate a SessionSummary from a list of chat messages."""
        if not messages:
            return None

        summary = SessionSummary(
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            workspace=workspace,
        )

        summary.summary = self._build_summary(messages)
        summary.key_decisions = self._extract_decisions(messages)
        summary.user_preferences = self._extract_preferences(messages)
        summary.open_tasks = self._extract_tasks(messages)
        summary.files_touched = self._extract_files(messages)

        return summary

    # ── Summary sentences ────────────────────────────────────────────

    def _build_summary(self, messages: list[dict]) -> str:
        """Build a 1–3 sentence narrative summary."""
        assistant_contents = [
            m.get("content", "") or ""
            for m in messages
            if m.get("role") == "assistant"
        ]

        if not assistant_contents:
            return ""

        # Score sentences
        scored: list[tuple[str, float]] = []
        for msg_idx, content in enumerate(assistant_contents):
            sentences = _SENTENCE_SPLIT.split(content.strip())
            for sent_idx, sentence in enumerate(sentences):
                sentence = sentence.strip()
                if len(sentence) < 10:
                    continue
                score = self._score_sentence(sentence, msg_idx, sent_idx)
                scored.append((sentence, score))

        if not scored:
            return ""

        # Pick top 3, deduplicate, join
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:3]
        top.sort(key=lambda x: scored.index(x))  # restore original order

        sentences = [s for s, _ in top]
        # Deduplicate near-identical sentences
        deduped: list[str] = []
        for s in sentences:
            if not any(self._similar(s, d) for d in deduped):
                deduped.append(s)

        return " ".join(deduped)

    def _score_sentence(self, sentence: str, msg_idx: int, sent_idx: int) -> float:
        """Score a sentence for summary inclusion."""
        score = 0.0
        lower = sentence.lower()

        # Position bonuses
        if msg_idx == 0 and sent_idx == 0:
            score += 2.0
        elif sent_idx == 0:
            score += 1.0

        # Action verb bonus
        for verb in _ACTION_VERBS:
            if verb in lower:
                score += 1.5
                break

        # Number bonus
        if re.search(r"\d+", sentence):
            score += 0.5

        # Length penalty
        length = len(sentence)
        if length < 20:
            score -= 1.0
        elif length > 300:
            score -= 0.5

        return score

    def _similar(self, a: str, b: str) -> bool:
        """Quick similarity check for deduplication."""
        a_norm = re.sub(r"\s+", " ", a.lower().strip())
        b_norm = re.sub(r"\s+", " ", b.lower().strip())
        return a_norm == b_norm or (len(a_norm) > 20 and a_norm in b_norm) or (len(b_norm) > 20 and b_norm in a_norm)

    # ── Key decisions ──────────────────────────────────────────────

    def _extract_decisions(self, messages: list[dict]) -> list[str]:
        """Extract sentences containing decision patterns."""
        results: list[str] = []
        pattern = re.compile("|".join(_DECISION_PATTERNS), re.IGNORECASE)

        for m in messages:
            content = m.get("content", "") or ""
            for sentence in _SENTENCE_SPLIT.split(content):
                sentence = sentence.strip()
                if pattern.search(sentence) and len(sentence) > 15:
                    results.append(sentence)

        return self._dedup_limit(results, 5)

    # ── User preferences ───────────────────────────────────────────

    def _extract_preferences(self, messages: list[dict]) -> list[str]:
        """Extract sentences containing preference patterns from user messages."""
        results: list[str] = []
        pattern = re.compile("|".join(_PREFERENCE_PATTERNS), re.IGNORECASE)

        for m in messages:
            if m.get("role") != "user":
                continue
            content = m.get("content", "") or ""
            for sentence in _SENTENCE_SPLIT.split(content):
                sentence = sentence.strip()
                if pattern.search(sentence) and len(sentence) > 10:
                    results.append(sentence)

        # Also capture explicit corrections
        correction_pattern = re.compile(r"\b(no,|actually,|correction:|wait,|instead,)\b", re.IGNORECASE)
        for m in messages:
            if m.get("role") != "user":
                continue
            content = m.get("content", "") or ""
            for sentence in _SENTENCE_SPLIT.split(content):
                sentence = sentence.strip()
                if correction_pattern.search(sentence) and sentence not in results:
                    results.append(sentence)

        return self._dedup_limit(results, 5)

    # ── Open tasks ─────────────────────────────────────────────────

    def _extract_tasks(self, messages: list[dict]) -> list[str]:
        """Extract sentences containing task/TODO patterns."""
        results: list[str] = []
        pattern = re.compile("|".join(_TASK_PATTERNS), re.IGNORECASE)

        for m in messages:
            content = m.get("content", "") or ""
            for sentence in _SENTENCE_SPLIT.split(content):
                sentence = sentence.strip()
                if pattern.search(sentence) and len(sentence) > 10:
                    results.append(sentence)

        return self._dedup_limit(results, 5)

    # ── Files touched ────────────────────────────────────────────────

    def _extract_files(self, messages: list[dict]) -> list[str]:
        """Extract file paths mentioned in assistant messages and tool calls."""
        seen: set[str] = set()
        results: list[str] = []

        for m in messages:
            # From message content
            content = m.get("content", "") or ""
            for match in _FILE_PATTERN.finditer(content):
                path = match.group(0)
                # Clean up: remove trailing punctuation
                path = path.rstrip(".,;:!?)")
                if path not in seen:
                    seen.add(path)
                    results.append(path)

            # From tool calls
            tool_calls = m.get("tool_calls", [])
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    args = fn.get("arguments", {}) if isinstance(fn, dict) else {}
                    if isinstance(args, dict):
                        for val in args.values():
                            if isinstance(val, str):
                                for match in _FILE_PATTERN.finditer(val):
                                    path = match.group(0).rstrip(".,;:!?)")
                                    if path not in seen:
                                        seen.add(path)
                                        results.append(path)

            if len(results) >= 10:
                break

        return results

    # ── Helpers ──────────────────────────────────────────────────────

    def _dedup_limit(self, items: list[str], limit: int) -> list[str]:
        """Deduplicate similar items and cap at limit."""
        deduped: list[str] = []
        for item in items:
            if not any(self._similar(item, d) for d in deduped):
                deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped
