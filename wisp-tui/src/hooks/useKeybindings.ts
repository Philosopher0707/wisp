import { useInput } from 'ink';
import { useAppState } from '../state/context.js';

export function useKeybindings() {
  const { state, dispatch, sendMessage } = useAppState();

  useInput((input, key) => {
    if (key.escape) {
      if (state.approvalPending) {
        sendMessage({
          type: 'tool_approval',
          id: state.approvalPending.callId,
          approved: false,
        });
        dispatch({ type: 'DENY_TOOL', callId: state.approvalPending.callId });
      }
      return;
    }

    if (key.ctrl) {
      if (input === 'c') {
        if (state.isStreaming) {
          sendMessage({ type: 'interrupt' });
          dispatch({ type: 'INTERRUPT' });
        } else {
          process.exit(0);
        }
      }
      if (input === 'l') {
        dispatch({ type: 'CLEAR_CHAT' });
      }
      if (input === 't') {
        dispatch({ type: 'TOGGLE_THINKING' });
      }
      if (input === 'n') {
        sendMessage({ type: 'new_session' });
        dispatch({ type: 'NEW_SESSION' });
      }
      return;
    }

    if (key.return) return;

    // Approval keys when prompt is pending
    if (state.approvalPending) {
      if (input === 'y') {
        sendMessage({
          type: 'tool_approval',
          id: state.approvalPending.callId,
          approved: true,
        });
        dispatch({ type: 'APPROVE_TOOL', callId: state.approvalPending.callId });
      } else if (input === 'n') {
        sendMessage({
          type: 'tool_approval',
          id: state.approvalPending.callId,
          approved: false,
        });
        dispatch({ type: 'DENY_TOOL', callId: state.approvalPending.callId });
      }
    }
  });
}
