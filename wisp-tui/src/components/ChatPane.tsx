import React from 'react';
import { Box, Text } from 'ink';
import { useAppState } from '../state/context.js';
import { Message } from './Message.js';

const VIEWPORT_MESSAGES = 20; // Approximate message capacity

export const ChatPane: React.FC = () => {
  const { state } = useAppState();

  const total = state.messages.length;
  // scrollOffset = 0 means "show from bottom" (most recent)
  // scrollOffset > 0 means "shift up by N messages"
  let start: number;
  if (total <= VIEWPORT_MESSAGES) {
    start = 0;
  } else if (state.scrollOffset === 0) {
    start = total - VIEWPORT_MESSAGES;
  } else {
    start = Math.max(0, total - VIEWPORT_MESSAGES - state.scrollOffset);
  }
  const end = Math.min(start + VIEWPORT_MESSAGES, total);
  const visibleMessages = state.messages.slice(start, end);
  const hasMoreAbove = start > 0;
  const hasMoreBelow = end < total;
  const atBottom = !hasMoreBelow && state.scrollOffset === 0;

  return (
    <Box flexDirection="column" flexGrow={1} paddingX={1}>
      {/* Scroll-up indicator */}
      {hasMoreAbove && (
        <Box marginY={1}>
          <Text dimColor>
            ↑ {start} older messages above — Ctrl+B scroll up | Ctrl+E jump to bottom
          </Text>
        </Box>
      )}

      {/* Empty state */}
      {visibleMessages.length === 0 && (
        <Box paddingY={1}>
          <Text dimColor>Start a conversation by typing below...</Text>
        </Box>
      )}

      {/* Messages */}
      {visibleMessages.map((msg) => (
        <Message key={msg.id} msg={msg} showThinking={state.showThinking} />
      ))}

      {/* Scroll-down / follow indicator */}
      {hasMoreBelow && (
        <Box marginY={1}>
          <Text dimColor>
            ↓ {total - end} newer messages below — Ctrl+F scroll down | Ctrl+E jump to bottom
          </Text>
        </Box>
      )}

      {/* Auto-follow indicator when scrolled away during streaming */}
      {!atBottom && state.isStreaming && (
        <Box marginY={1}>
          <Text color="yellow">
            ◉ Streaming... (Ctrl+E to follow)
          </Text>
        </Box>
      )}
    </Box>
  );
};
