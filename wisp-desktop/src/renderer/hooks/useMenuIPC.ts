import { useEffect, Dispatch } from 'react';
import type { Action } from '../state/types.js';

export function useMenuIPC(
  dispatch: Dispatch<Action>,
  serverUrl?: string,
  apiKey?: string,
) {
  useEffect(() => {
    if (!window.wisp?.onMenuAction) return;
    const unsub = window.wisp.onMenuAction((action: string) => {
      switch (action) {
        case 'new-chat':
          dispatch({ type: 'NEW_CHAT' });
          break;
        case 'open-workspace': {
          window.wisp.selectDirectory().then(async (dirPath) => {
            if (!dirPath) return;
            // Update backend workspace via POST /api/workspace
            if (serverUrl) {
              const base = serverUrl.replace(/\/$/, '');

              try {
                const resp = await fetch(`${base}/api/workspace`, {
                  method: 'POST',
                  headers: {
                    'Content-Type': 'application/json',
                    ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
                  },
                  body: JSON.stringify({ path: dirPath }),
                });
                const data = await resp.json() as { path?: string };
                if (data.path) {
                  dispatch({ type: 'SET_WORKSPACE', path: data.path });
                }
              } catch {
                // Fallback: update frontend state only
                dispatch({ type: 'SET_WORKSPACE', path: dirPath });
              }
            } else {
              dispatch({ type: 'SET_WORKSPACE', path: dirPath });
            }
          });
          break;
        }
        case 'open-file':
          window.wisp.openFileDialog().then((paths) => {
            if (paths && paths.length > 0) {
              dispatch({
                type: 'RECEIVE_STATUS',
                message: `Attached: ${paths.map((p) => p.split('/').pop()).join(', ')}`,
                level: 'info',
              });
            }
          });
          break;
        case 'find':
          dispatch({ type: 'OPEN_OVERLAY', overlay: 'search' });
          break;
        case 'toggle-thinking':
          dispatch({ type: 'TOGGLE_THINKING' });
          break;
        case 'clear-chat':
          dispatch({ type: 'CLEAR_CHAT' });
          break;
      }
    });
    return unsub;
  }, [dispatch, serverUrl, apiKey]);
}
