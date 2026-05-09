import React from 'react';
import { Box, Text } from 'ink';
import TextInput from 'ink-text-input';
import { useAppState } from '../state/context.js';

export const InputBar: React.FC = () => {
  const { state, dispatch, sendMessage } = useAppState();

  const handleSubmit = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;

    // Slash commands
    if (trimmed.startsWith('/')) {
      const parts = trimmed.slice(1).split(/\s+/);
      const cmd = parts[0];
      switch (cmd) {
        case 'help':
        case '?':
          dispatch({ type: 'RECEIVE_STATUS', message: 'Commands: /new, /thinking, /model <name>, /clear, /quit', level: 'info' });
          return;
        case 'new':
        case 'session':
          sendMessage({ type: 'new_session' });
          dispatch({ type: 'NEW_SESSION' });
          return;
        case 'thinking':
          dispatch({ type: 'TOGGLE_THINKING' });
          return;
        case 'clear':
          dispatch({ type: 'CLEAR_CHAT' });
          return;
        case 'quit':
        case 'exit':
        case 'q':
          process.exit(0);
          return;
        case 'model':
          if (parts[1]) {
            dispatch({ type: 'SET_MODEL', model: parts[1] });
            dispatch({ type: 'RECEIVE_STATUS', message: `Model set to ${parts[1]}`, level: 'info' });
          }
          return;
      }
    }

    dispatch({ type: 'SUBMIT_PROMPT', content: trimmed });
    sendMessage({
      type: 'prompt',
      content: trimmed,
      session_id: state.sessionId || undefined,
      model: state.selectedModel || undefined,
      show_thinking: state.showThinking,
    });
  };

  return (
    <Box flexDirection="row" paddingX={1} borderStyle="single" borderColor="gray">
      <Text color="cyan">{'>'} </Text>
      <TextInput
        value={state.inputValue}
        onChange={(v) => dispatch({ type: 'SET_INPUT', value: v })}
        onSubmit={handleSubmit}
        placeholder={state.isStreaming ? 'Waiting for agent...' : 'Type a message...'}
        focus={!state.isStreaming}
      />
    </Box>
  );
};
