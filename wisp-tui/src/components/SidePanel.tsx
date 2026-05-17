import React, { useEffect } from 'react';
import { Box, Text } from 'ink';
import { useAppState } from '../state/context.js';

export const SidePanel: React.FC = () => {
  const { state, dispatch } = useAppState();
  const sessions = state.sessions;

  useEffect(() => {
    fetch(`${state.serverUrl}/api/sessions`)
      .then((r) => r.json())
      .then((data) => dispatch({ type: 'SET_SESSIONS', sessions: data.sessions }))
      .catch(() => {});
  }, [state.serverUrl, state.sessionId, dispatch]);

  const currentSession = sessions.find((s) => s.id === state.sessionId);

  return (
    <Box flexDirection="column" width="25%" borderStyle="single" borderColor="gray" paddingX={1}>
      {/* Threads list */}
      <Box marginBottom={1}>
        <Text bold>Threads</Text>
        <Text dimColor> ({sessions.length})</Text>
      </Box>
      <Box flexDirection="column" marginBottom={1}>
        {sessions.slice(0, 15).map((s) => {
          const isActive = s.id === state.sessionId;
          return (
            <Box key={s.id} flexDirection="column">
              <Text color={isActive ? 'cyan' : undefined} bold={isActive}>
                {isActive ? '▶ ' : '  '}{s.title || s.id.slice(0, 12)}
              </Text>
              <Text dimColor>     {s.model} · {s.message_count} msgs</Text>
            </Box>
          );
        })}
        {sessions.length === 0 && (
          <Text dimColor>  No threads yet</Text>
        )}
      </Box>

      {/* Separator  */}
      <Box marginY={1}>
        <Text dimColor>─────────────────</Text>
      </Box>

      {/* Current session details */}
      <Box marginBottom={1}>
        <Text bold>Details</Text>
      </Box>
      {currentSession ? (
        <Box flexDirection="column">
          <Box>
            <Text dimColor>ID: </Text>
            <Text>{currentSession.id.slice(0, 20)}</Text>
          </Box>
          <Box>
            <Text dimColor>Model: </Text>
            <Text>{currentSession.model}</Text>
          </Box>
          <Box>
            <Text dimColor>Messages: </Text>
            <Text>{currentSession.message_count}</Text>
          </Box>
          <Box>
            <Text dimColor>Created: </Text>
            <Text>{currentSession.created_at?.slice(0, 16) || '-'}</Text>
          </Box>
          <Box>
            <Text dimColor>Workspace: </Text>
            <Text>{currentSession.workspace?.split('/').slice(-2).join('/') || '-'}</Text>
          </Box>
        </Box>
      ) : (
        <Box flexDirection="column">
          <Text dimColor>New session</Text>
          <Text dimColor>Model: {state.selectedModel || 'default'}</Text>
        </Box>
      )}

      {/* Actions */}
      <Box marginY={1}>
        <Text dimColor>─────────────────</Text>
      </Box>
      <Box flexDirection="column">
        <Text color="cyan">[n] New thread</Text>
        <Text color="cyan">[Ctrl+C] Quit</Text>
        <Text dimColor>[/] Commands</Text>
      </Box>
    </Box>
  );
};
