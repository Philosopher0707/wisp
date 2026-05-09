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

from wisp.core.message_format import extract_text

logger = logging.getLogger(__name__)


def _get_content(msg: dict) -> str:
    """Extract plain text from a message, handling multimodal content arrays."""
    return extract_text(msg.get("content", "") or "")


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
    thread_stack: list[dict] = field(default_factory=list)

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
            "thread_stack": self.thread_stack,
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
            thread_stack=data.get("thread_stack", []),
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
        summary.thread_stack = self._extract_thread_stack(messages)

        return summary

    # ── Summary sentences ────────────────────────────────────────────

    def _build_summary(self, messages: list[dict]) -> str:
        """Build a 1–3 sentence narrative summary."""
        assistant_contents = [
            _get_content(m)
            for m in messages
            if m.get("role") == "assistant"
        ]

        if not assistant_contents:
            return ""

        # Score sentences — store (sentence, score, original_index) for stable ordering
        scored: list[tuple[str, float, int]] = []
        idx = 0
        total_msgs = len(assistant_contents)
        for msg_idx, content in enumerate(assistant_contents):
            sentences = _SENTENCE_SPLIT.split(content.strip())
            for sent_idx, sentence in enumerate(sentences):
                sentence = sentence.strip()
                if len(sentence) < 10:
                    continue
                score = self._score_sentence(sentence, msg_idx, sent_idx, total_msgs)
                scored.append((sentence, score, idx))
                idx += 1

        if not scored:
            return ""

        # Pick top 3 by score
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:3]
        # Restore original order using stored index
        top.sort(key=lambda x: x[2])

        sentences = [s for s, _, _ in top]
        # Deduplicate near-identical sentences
        deduped: list[str] = []
        for s in sentences:
            if not any(self._similar(s, d) for d in deduped):
                deduped.append(s)

        return " ".join(deduped)

    def _score_sentence(self, sentence: str, msg_idx: int, sent_idx: int, total_msgs: int = 1) -> float:
        """Score a sentence for summary inclusion.

        Rewards sentences from later messages (conclusions > intros),
        action verbs, and concrete details. Penalizes too-short or too-long sentences.
        """
        score = 0.0
        lower = sentence.lower()

        # Position bonuses — favor later messages (conclusions over boilerplate intros)
        position_ratio = (msg_idx + 1) / max(total_msgs, 1)
        if sent_idx == 0 and msg_idx > 0:
            score += 1.0 * position_ratio  # opening sentence of later messages
        elif msg_idx == total_msgs - 1:
            score += 2.0  # last message — most likely to contain conclusion

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
            content = _get_content(m)
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
            content = _get_content(m)
            for sentence in _SENTENCE_SPLIT.split(content):
                sentence = sentence.strip()
                if pattern.search(sentence) and len(sentence) > 10:
                    results.append(sentence)

        # Also capture explicit corrections
        correction_pattern = re.compile(r"\b(no,|actually,|correction:|wait,|instead,)\b", re.IGNORECASE)
        for m in messages:
            if m.get("role") != "user":
                continue
            content = _get_content(m)
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
            content = _get_content(m)
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
        _MAX_FILES = 30

        for m in messages:
            # From message content
            content = _get_content(m)
            for match in _FILE_PATTERN.finditer(content):
                path = match.group(0)
                path = path.rstrip(".,;:!?)")
                if path not in seen and len(path) < 200:
                    seen.add(path)
                    results.append(path)
                    if len(results) >= _MAX_FILES:
                        return results

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
                                    if path not in seen and len(path) < 200:
                                        seen.add(path)
                                        results.append(path)
                                        if len(results) >= _MAX_FILES:
                                            return results

        return results

    # ── Thread stack ─────────────────────────────────────────────────

    _INCOMPLETE_MARKERS = [
        "here are the steps", "first,", "1.", "step 1", "let me start",
        "i will", "we will", "coming up", "next, we",
        "to begin", "starting with", "part 1", "option 1",
    ]

    def _extract_thread_stack(self, messages: list[dict]) -> list[dict]:
        """Identify active or incomplete discussion threads from assistant messages.

        Walks backwards through the conversation to find the last few assistant
        turns. Each turn is scored for completeness (terminal punctuation, no
        incomplete markers). The last turn flagged INCOMPLETE tells compaction
        what to resume on 'continue'. Keeps up to 3 threads.
        """
        stack: list[dict] = []

        # Collect (msg_index, assistant_msg, user_prompt) for each turn
        turns: list[tuple[int, dict, str]] = []
        for i, m in enumerate(messages):
            if m.get("role") != "assistant":
                continue
            content = (_get_content(m)).strip()
            if not content:
                continue
            # Find preceding user prompt
            user_prompt = ""
            for prev in reversed(messages[:i]):
                if prev.get("role") == "user":
                    user_prompt = (prev.get("content", "") or "")[:200]
                    break
            turns.append((i, m, user_prompt))

        if not turns:
            return stack

        # Take last 3 turns, walk backwards for status
        recent = turns[-3:]
        for idx, msg, user_prompt in reversed(recent):
            content = _get_content(msg).strip()
            lower = content.lower()
            no_terminal = not bool(re.search(r"[.!?```}\])]$", content[-5:]))
            has_marker = any(marker in lower for marker in self._INCOMPLETE_MARKERS)
            looks_incomplete = no_terminal or has_marker

            topic = content[:120].replace("\n", " ")
            stack.append({
                "topic": topic,
                "status": "INCOMPLETE" if looks_incomplete else "COMPLETE",
                "last_sub_topic": "",
                "prompt": user_prompt,
            })

        return stack

    # ── Helpers ──────────────────────────────────────────────────────

    _MAX_ITEM_LENGTH = 300

    def _dedup_limit(self, items: list[str], limit: int) -> list[str]:
        """Deduplicate similar items, cap individual length, and cap at limit."""
        deduped: list[str] = []
        for item in items:
            trimmed = item.strip()[:self._MAX_ITEM_LENGTH]
            if len(trimmed) < 10:
                continue
            if not any(self._similar(trimmed, d) for d in deduped):
                deduped.append(trimmed)
            if len(deduped) >= limit:
                break
        return deduped
