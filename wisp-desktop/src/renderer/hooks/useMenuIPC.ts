import { useEffect, Dispatch } from 'react';
import type { Action } from '../state/types.js';

export function useMenuIPC(dispatch: Dispatch<Action>) {
  useEffect(() => {
    const unsub = window.wisp.onMenuAction((action: string) => {
      switch (action) {
        case 'new-chat':
          dispatch({ type: 'NEW_CHAT' });
          break;
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
          // Focus search — handled by SearchModal state
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
  }, [dispatch]);
}
