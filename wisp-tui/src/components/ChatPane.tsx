import React from 'react';
import { Box, Text } from 'ink';
import { useAppState } from '../state/context.js';
import { Message } from './Message.js';

export const ChatPane: React.FC = () => {
  const { state } = useAppState();

  // Show last N messages that fit in the terminal
  const visibleMessages = state.messages;

  return (
    <Box flexDirection="column" flexGrow={1} paddingX={1}>
      {visibleMessages.length === 0 && (
        <Box paddingY={1}>
          <Text dimColor>Start a conversation by typing below...</Text>
        </Box>
      )}
      {visibleMessages.map((msg) => (
        <Message key={msg.id} msg={msg} />
      ))}
    </Box>
  );
};
