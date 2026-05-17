import React from 'react';
import { Box, Text } from 'ink';
import type { UIMessage } from '../state/types.js';
import { ToolCallBanner } from './ToolCallBanner.js';

interface Props {
  msg: UIMessage;
  showThinking: boolean;
}

function renderMarkdown(text: string): string {
  // Basic terminal markdown: strip formatting codes that don't render in terminal
  return text
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/^#{1,6}\s/gm, '');
}

export const Message: React.FC<Props> = ({ msg, showThinking }) => {
  return (
    <Box flexDirection="column" marginY={1}>
      {msg.role === 'user' && (
        <Box>
          <Text color="cyan" bold>{'>'} {renderMarkdown(msg.content)}</Text>
        </Box>
      )}

      {msg.role === 'assistant' && (
        <Box flexDirection="column">
          {msg.thinking && showThinking && (
            <Box>
              <Text dimColor>[thinking] {renderMarkdown(msg.thinking.slice(-200))}</Text>
            </Box>
          )}
          {msg.content && (
            <Box>
              <Text>{renderMarkdown(msg.content)}</Text>
            </Box>
          )}
          {msg.toolCalls?.map((tc, i) => (
            <ToolCallBanner key={i} toolCall={tc} />
          ))}
        </Box>
      )}

      {msg.role === 'system' && (
        <Box>
          <Text dimColor>{renderMarkdown(msg.content)}</Text>
        </Box>
      )}
    </Box>
  );
};
