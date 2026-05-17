import React from 'react';
import { Box, Text } from 'ink';
import { useWebSocket } from '../hooks/useWebSocket.js';
import { useKeybindings } from '../hooks/useKeybindings.js';
import { AppContext } from '../state/context.js';
import { appReducer, createInitialState } from '../state/types.js';
import { SessionPicker } from './SessionPicker.js';
import { Header } from './Header.js';
import { ChatPane } from './ChatPane.js';
import { InputBar } from './InputBar.js';
import { StatusBar } from './StatusBar.js';
import { SidePanel } from './SidePanel.js';
import { ApprovalPrompt } from './ApprovalPrompt.js';

interface Props {
  serverUrl: string;
}

export const App: React.FC<Props> = ({ serverUrl }) => {
  const [state, dispatch] = React.useReducer(appReducer, serverUrl, createInitialState);
  const ws = useWebSocket(serverUrl, dispatch);

  const ctx = React.useMemo(
    () => ({
      state,
      dispatch,
      sendMessage: (msg: { type: string; [key: string]: unknown }) => ws.send(msg),
    }),
    [state, dispatch, ws.send],
  );

  useKeybindings();

  return (
    <AppContext.Provider value={ctx}>
      <Box flexDirection="column" minHeight={process.stdout.rows || 24}>
        {state.viewMode === 'session-picker' ? (
          <SessionPicker />
        ) : (
          <>
            <Header />
            <Box flexDirection="row" flexGrow={1}>
              {/* Left sidebar — always visible */}
              <SidePanel />
              {/* Main chat area — takes remaining width */}
              <Box flexDirection="column" flexGrow={1} borderStyle="single" borderColor="gray">
                <ChatPane />
                {state.approvalPending && <ApprovalPrompt />}
                <InputBar />
              </Box>
            </Box>
            <StatusBar />
          </>
        )}
      </Box>
    </AppContext.Provider>
  );
};
