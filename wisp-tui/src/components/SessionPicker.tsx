import React, { useEffect } from 'react';
import { Box, Text, useInput } from 'ink';
import { useAppState } from '../state/context.js';

export const SessionPicker: React.FC = () => {
  const { state, dispatch } = useAppState();
  const [selectedIdx, setSelectedIdx] = React.useState(0);

  useEffect(() => {
    fetch(`${state.serverUrl}/api/sessions`)
      .then((r) => r.json())
      .then((data) => {
        dispatch({ type: 'SET_SESSIONS', sessions: data.sessions });
      })
      .catch(() => {
        // server may not be running yet
      });
  }, [state.serverUrl, dispatch]);

  const sessions = state.sessions;

  useInput((input, key) => {
    if (key.upArrow && selectedIdx > 0) setSelectedIdx(selectedIdx - 1);
    if (key.downArrow && selectedIdx < sessions.length) setSelectedIdx(selectedIdx + 1);
    if (key.return) {
      if (selectedIdx < sessions.length) {
        const s = sessions[selectedIdx];
        fetch(`${state.serverUrl}/api/sessions/${s.id}`)
          .then((r) => r.json())
          .then((data) => {
            const msgs = (data.session?.messages || []).map((m: Record<string, unknown>) => ({
              id: `hist-${Math.random().toString(36).slice(2, 8)}`,
              role: m.role as 'user' | 'assistant' | 'system',
              content: (m.content as string) || '',
              thinking: (m.thinking as string) || undefined,
            }));
            dispatch({ type: 'SELECT_SESSION', sessionId: s.id, messages: msgs });
          })
          .catch(() => {
            dispatch({ type: 'NEW_SESSION' });
          });
      } else {
        dispatch({ type: 'NEW_SESSION' });
      }
    }
    if (input === 'n') dispatch({ type: 'NEW_SESSION' });
    if (input === 'q') process.exit(0);
  });

  function connectionLabel(): string {
    switch (state.connection) {
      case 'connected': return 'connected';
      case 'connecting': return 'connecting...';
      case 'disconnected': return 'disconnected';
      case 'error': return 'error';
      default: return state.connection;
    }
  }

  return (
    <Box flexDirection="column" padding={1}>
      <Box flexDirection="column" marginBottom={1}>
        <Text bold>Wisp — Sessions</Text>
        <Text dimColor>Arrow keys to select, Enter to load, [n] new session, [q] quit</Text>
        <Text dimColor>
          Server: {state.serverUrl} ({connectionLabel()})
        </Text>
      </Box>
      <Box flexDirection="column">
        {sessions.map((s, i) => (
          <Box key={s.id}>
            <Text color={i === selectedIdx ? 'cyan' : undefined}>
              {i === selectedIdx ? '▶' : ' '} {s.title || s.id}
            </Text>
            <Text dimColor> — {s.model} — {s.message_count} msgs — {s.updated_at?.slice(0, 16) || s.created_at?.slice(0, 16)}</Text>
          </Box>
        ))}
        <Box marginTop={2}>
          <Text color={selectedIdx === sessions.length ? 'cyan' : undefined}>
            {selectedIdx === sessions.length ? '▶' : ' '} [New Session]
          </Text>
        </Box>
      </Box>
      {state.connection === 'disconnected' && (
        <Box marginTop={1}>
          <Text color="yellow">Server not reachable. Start with: wisp server --no-auth</Text>
        </Box>
      )}
    </Box>
  );
};
