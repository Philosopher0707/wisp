import React from 'react';
import { Box, Text } from 'ink';
import type { ToolCallItem } from '../state/types.js';

interface Props {
  toolCall: ToolCallItem;
}

function argsPreview(args: Record<string, unknown>): string {
  const path = (args.path || args.command || '') as string;
  if (path) return String(path).slice(0, 60);
  const content = (args.content || '') as string;
  if (content) return `(${content.length} chars)`;
  return '...';
}

function resultPreview(result: string): string {
  return result.slice(0, 200).replace(/\n/g, ' ');
}

export const ToolCallBanner: React.FC<Props> = ({ toolCall }) => {
  const [expanded, setExpanded] = React.useState(false);

  return (
    <Box flexDirection="column" marginLeft={1}>
      <Box>
        <Text dimColor>
          {toolCall.durationMs ? '  ✓' : '  ⏳'} {toolCall.name}({argsPreview(toolCall.args)})
        </Text>
        {toolCall.durationMs && (
          <Text dimColor> ({toolCall.durationMs.toFixed(0)}ms)</Text>
        )}
      </Box>
      {expanded && toolCall.result && (
        <Box marginLeft={2}>
          <Text dimColor>{resultPreview(toolCall.result)}</Text>
        </Box>
      )}
    </Box>
  );
};
