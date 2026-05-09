import React from 'react';
import { Box, Text } from 'ink';
import { useAppState } from '../state/context.js';

export const Header: React.FC = () => {
  const { state } = useAppState();

  const connColor = state.connection === 'connected' ? 'green' : state.connection === 'connecting' ? 'yellow' : 'red';
  const connDot = state.connection === 'connected' ? '●' : state.connection === 'connecting' ? '◐' : '○';
  const modelName = state.selectedModel || 'default';
  const sessionLabel = state.sessionId ? state.sessionId.slice(0, 16) : 'new session';

  return (
    <Box flexDirection="row" justifyContent="space-between" paddingX={1}>
      <Box>
        <Text bold>Wisp</Text>
        <Text dimColor> · {modelName} · {sessionLabel}</Text>
      </Box>
      <Box>
        <Text color={connColor}>{connDot}</Text>
        <Text dimColor> {state.connection}</Text>
        <Text dimColor> | messages: {state.messages.length}</Text>
      </Box>
    </Box>
  );
};
