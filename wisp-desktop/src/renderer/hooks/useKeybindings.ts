import { useEffect } from 'react';
import { useAppState } from '../state/context.js';

export function useKeybindings() {
  const { state, dispatch, sendMessage } = useAppState();

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const isMeta = e.metaKey || e.ctrlKey;

      // Approval keys — highest priority when approval is pending
      if (state.approvalPending) {
        if (e.key === 'y' && !isMeta) {
          e.preventDefault();
          sendMessage({
            type: 'tool_approval',
            id: state.approvalPending.callId,
            approved: true,
          });
          dispatch({ type: 'APPROVE_TOOL', callId: state.approvalPending.callId });
          return;
        }
        if (e.key === 'n' && !isMeta) {
          e.preventDefault();
          sendMessage({
            type: 'tool_approval',
            id: state.approvalPending.callId,
            approved: false,
            reason: 'User denied',
          });
          dispatch({ type: 'DENY_TOOL', callId: state.approvalPending.callId });
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          sendMessage({
            type: 'tool_approval',
            id: state.approvalPending.callId,
            approved: false,
            reason: 'User denied',
          });
          dispatch({ type: 'DENY_TOOL', callId: state.approvalPending.callId });
          return;
        }
      }

      // Escape — close dropdown
      if (e.key === 'Escape') {
        if (state.activeDropdown) {
          dispatch({ type: 'CLOSE_DROPDOWN' });
          return;
        }
      }

      // Cmd/Ctrl shortcuts
      if (isMeta) {
        switch (e.key.toLowerCase()) {
          case 'n':
            e.preventDefault();
            dispatch({ type: 'NEW_CHAT' });
            sendMessage({ type: 'new_session' });
            return;
          case 'k':
            e.preventDefault();
            // Focus search modal (future)
            return;
          case 'l':
            e.preventDefault();
            dispatch({ type: 'CLEAR_CHAT' });
            return;
          case 't':
            e.preventDefault();
            dispatch({ type: 'TOGGLE_THINKING' });
            return;
          case 'c':
            if (state.isStreaming) {
              e.preventDefault();
              sendMessage({ type: 'interrupt' });
              dispatch({ type: 'INTERRUPT' });
            }
            return;
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [state.approvalPending, state.activeDropdown, state.isStreaming, dispatch, sendMessage]);
}
