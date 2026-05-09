import React from 'react';
import { Box, Text } from 'ink';
import { useAppState } from '../state/context.js';

export const ApprovalPrompt: React.FC = () => {
  const { state } = useAppState();
  if (!state.approvalPending) return null;

  const { toolName, args, reason } = state.approvalPending;
  const path = ((args.path || args.command || '') as string).slice(0, 60);

  return (
    <Box flexDirection="column" marginLeft={2} marginY={1}>
      <Box>
        <Text color="yellow" bold>
          ⚠ DANGEROUS: {reason}
        </Text>
      </Box>
      <Box>
        <Text>
          Approve {toolName}({path || '...'})?{' '}
          <Text color="green" bold>[y]</Text>es /{' '}
          <Text color="red" bold>[n]</Text>o
        </Text>
      </Box>
    </Box>
  );
};
