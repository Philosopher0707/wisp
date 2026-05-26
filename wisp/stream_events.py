"""Typed event system for streaming LLM responses.

Separates content (stdout) from metadata (typed events) for clean I/O handling.
"""

from dataclasses import dataclass
from typing import Iterator, Literal
import hashlib


@dataclass(frozen=True)
class StreamEvent:
    """Base class for all stream events."""
    phase: Literal["thinking", "content", "tool_calls", "checkpoint", "complete", "error"]
    

@dataclass(frozen=True)
class TokenBatch(StreamEvent):
    """Batch of tokens for a specific phase.
    
    Buffers tokens to reduce I/O operations - yields batches instead of individual tokens.
    """
    phase: Literal["thinking", "content"]
    text: str
    batch_index: int
    
    def __post_init__(self):
        if not isinstance(self.text, str):
            raise TypeError(f"TokenBatch.text must be str, got {type(self.text)}")


@dataclass(frozen=True)  
class ToolCallBatch(StreamEvent):
    """Tool calls from the model."""
    phase: Literal["tool_calls"]
    calls: list[dict]


@dataclass(frozen=True)
class Checkpoint(StreamEvent):
    """Verification checkpoint - not from LLM, but from our own state tracking.
    
    Used to validate stream integrity without trusting the LLM's "done" flag alone.
    """
    phase: Literal["checkpoint"]
    accumulated_thinking: str
    accumulated_content: str
    token_count: int
    checkpoint_hash: str  # Hash of accumulated text for validation
    
    @staticmethod
    def compute_hash(thinking: str, content: str) -> str:
        """Compute checkpoint hash for validation."""
        data = f"{thinking}:{content}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class StreamComplete(StreamEvent):
    """Stream successfully completed."""
    phase: Literal["complete"]
    final_thinking: str
    final_content: str
    total_tokens: int
    tool_calls: list[dict] | None
    validation_hash: str  # Must match last checkpoint
    done_reason: str = ""  # Ollama done_reason (e.g. "stop", "length")


@dataclass(frozen=True)
class StreamError(StreamEvent):
    """Stream encountered an error."""
    phase: Literal["error"]
    error_type: str
    message: str
    partial_thinking: str
    partial_content: str


class EventBatcher:
    """Batches tokens to reduce I/O operations.
    
    Instead of yielding each token as a separate event (I/O per token),
    we accumulate until threshold or phase change, then yield a batch.
    """
    
    def __init__(self, batch_size: int = 10, max_wait_chars: int = 100):
        self.batch_size = batch_size
        self.max_wait_chars = max_wait_chars
        self._thinking_buffer: list[str] = []
        self._content_buffer: list[str] = []
        self._thinking_chars = 0
        self._content_chars = 0
        self._batch_counter = 0
        
    def add_thinking(self, token: str) -> Iterator[TokenBatch]:
        """Add thinking token, yield batch if threshold reached."""
        self._thinking_buffer.append(token)
        self._thinking_chars += len(token)
        
        if len(self._thinking_buffer) >= self.batch_size or self._thinking_chars >= self.max_wait_chars:
            yield from self._flush_thinking()
            
    def add_content(self, token: str) -> Iterator[TokenBatch]:
        """Add content token, yield batch if threshold reached."""
        self._content_buffer.append(token)
        self._content_chars += len(token)
        
        if len(self._content_buffer) >= self.batch_size or self._content_chars >= self.max_wait_chars:
            yield from self._flush_content()
            
    def _flush_thinking(self) -> Iterator[TokenBatch]:
        """Flush thinking buffer."""
        if self._thinking_buffer:
            text = "".join(self._thinking_buffer)
            self._thinking_buffer = []
            self._thinking_chars = 0
            self._batch_counter += 1
            yield TokenBatch(phase="thinking", text=text, batch_index=self._batch_counter)
            
    def _flush_content(self) -> Iterator[TokenBatch]:
        """Flush content buffer."""
        if self._content_buffer:
            text = "".join(self._content_buffer)
            self._content_buffer = []
            self._content_chars = 0
            self._batch_counter += 1
            yield TokenBatch(phase="content", text=text, batch_index=self._batch_counter)
            
    def flush_all(self) -> Iterator[TokenBatch]:
        """Flush both buffers - call at end of stream or phase change."""
        yield from self._flush_thinking()
        yield from self._flush_content()
        
    def checkpoint(self, thinking: str, content: str, token_count: int) -> Checkpoint:
        """Create checkpoint from accumulated state."""
        return Checkpoint(
            phase="checkpoint",
            accumulated_thinking=thinking,
            accumulated_content=content,
            token_count=token_count,
            checkpoint_hash=Checkpoint.compute_hash(thinking, content)
        )
