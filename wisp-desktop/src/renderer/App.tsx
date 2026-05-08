import React from 'react';
import { AppShell } from './components/layout/AppShell.js';
import { AppContext } from './state/context.js';
import { appReducer, createInitialState } from './state/types.js';
import { useWebSocket } from './hooks/useWebSocket.js';

import { useMenuIPC } from './hooks/useMenuIPC.js';

interface Props {
  serverUrl: string;
  apiKey: string;
}

export const App: React.FC<Props> = ({ serverUrl, apiKey }) => {
  const [state, dispatch] = React.useReducer(
    appReducer,
    { serverUrl, apiKey },
    (opts) => createInitialState(opts),
  );
  const ws = useWebSocket(serverUrl, apiKey, dispatch);
  useMenuIPC(dispatch);

  const ctx = React.useMemo(
    () => ({ state, dispatch, sendMessage: (msg: Record<string, unknown>) => ws.send(msg) }),
    [state, dispatch, ws.send],
  );

  return (
    <AppContext.Provider value={ctx}>
      <AppShell />
    </AppContext.Provider>
  );
};
