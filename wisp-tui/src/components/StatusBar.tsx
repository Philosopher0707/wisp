import React from 'react';
import { Box, Text } from 'ink';
import { useAppState } from '../state/context.js';

export const StatusBar: React.FC = () => {
  const { state } = useAppState();

  const connColor = state.connection === 'connected' ? 'green' : state.connection === 'connecting' ? 'yellow' : 'red';
  const streamLabel = state.isStreaming ? ' STREAMING' : '';
  const tokenEstimate = Math.round(state.messages.reduce((n, m) => n + m.content.length + (m.thinking?.length || 0), 0) / 4);

  return (
    <Box flexDirection="row" paddingX={1}>
      <Box flexGrow={1}>
        <Text color={connColor}>{state.connection === 'connected' ? '●' : '○'}</Text>
        <Text dimColor> connected{streamLabel} | tokens: ~{tokenEstimate}K</Text>
      </Box>
      <Box>
        <Text dimColor>Ctrl+C quit | Ctrl+L clear | Ctrl+T thinking | Ctrl+N new</Text>
      </Box>
    </Box>
  );
};
