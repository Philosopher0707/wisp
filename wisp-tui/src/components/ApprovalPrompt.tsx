import React from 'react';
import { Box, Text } from 'ink';
import { useAppState } from '../state/context.js';

export const ApprovalPrompt: React.FC = () => {
  const { state } = useAppState();
  if (!state.approvalPending) return null;

  const { toolName, args, reason } = state.approvalPending;
  const path = ((args.path || args.command || '') as string).slice(0, 60);
  const modeLabel = state.autoApprove ? '[auto-approve ON]' : '';

  return (
    <Box flexDirection="column" marginLeft={2} marginY={1} borderStyle="single" borderColor="yellow" paddingX={1}>
      <Box>
        <Text color="yellow" bold>
          ⚠ DANGEROUS{modeLabel ? ` ${modeLabel}` : ''}: {reason}
        </Text>
      </Box>
      <Box>
        <Text>
          Approve {toolName}({path || '...'})?
        </Text>
      </Box>
      <Box marginTop={1}>
        <Text color="green" bold>[y]</Text>
        <Text>es / </Text>
        <Text color="red" bold>[n]</Text>
        <Text>o / </Text>
        <Text color="cyan" bold>[a]</Text>
        <Text>pprove all for session / </Text>
        <Text color="magenta" bold>[d]</Text>
        <Text>eny + stop asking</Text>
      </Box>
    </Box>
  );
};
