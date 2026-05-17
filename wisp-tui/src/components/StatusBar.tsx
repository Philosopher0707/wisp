import React from 'react';
import { Box, Text } from 'ink';
import { useAppState } from '../state/context.js';

interface StatusBarProps {
  scrollOffset?: number;
}

export const StatusBar: React.FC<StatusBarProps> = ({ scrollOffset = 0 }) => {
  const { state } = useAppState();

  const connColor = state.connection === 'connected' ? 'green' : state.connection === 'connecting' ? 'yellow' : 'red';
  const streamLabel = state.isStreaming ? ' STREAMING' : '';
  // Approximate tokens: ~4 chars per token, display as K (thousands)
  const totalChars = state.messages.reduce((n, m) => n + m.content.length + (m.thinking?.length || 0), 0);
  const tokenEstimate = Math.round(totalChars / 4000 * 10) / 10; // Round to 1 decimal

  const scrollInfo = scrollOffset > 0 ? `↑${scrollOffset} ` : '';

  return (
    <Box flexDirection="row" paddingX={1}>
      <Box flexGrow={1}>
        <Text color={connColor}>{state.connection === 'connected' ? '●' : '○'}</Text>
        <Text dimColor> connected{streamLabel} | tokens: ~{tokenEstimate}K{scrollInfo ? ' | ' + scrollInfo : ''}</Text>
      </Box>
      <Box>
        <Text dimColor>Ctrl+C quit | Ctrl+L clear | Ctrl+T thinking | Ctrl+N new | Ctrl+B/F scroll</Text>
      </Box>
    </Box>
  );
};
