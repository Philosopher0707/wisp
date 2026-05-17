import { useInput } from 'ink';
import { useAppState } from '../state/context.js';

export function useKeybindings() {
  const { state, dispatch, sendMessage } = useAppState();

  useInput((input, key) => {
    if (key.escape) {
      if (state.approvalPending) {
        _deny(dispatch, sendMessage, state.approvalPending.callId);
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
      if (input === 'b') {
        dispatch({ type: 'SCROLL_UP', lines: 5 });
      }
      if (input === 'f') {
        dispatch({ type: 'SCROLL_DOWN', lines: 5 });
      }
      if (input === 'e') {
        dispatch({ type: 'SCROLL_BOTTOM' });
      }
      return;
    }

    if (key.return) return;

    // ── Approval keys ───────────────────────────────────────────────
    if (state.approvalPending) {
      if (input === 'y') {
        // Approve once
        _approve(dispatch, sendMessage, state.approvalPending.callId);
      } else if (input === 'n') {
        // Deny once
        _deny(dispatch, sendMessage, state.approvalPending.callId);
      } else if (input === 'a') {
        // Enable auto-approve for this session
        dispatch({ type: 'ENABLE_AUTO_APPROVE' });
        _approve(dispatch, sendMessage, state.approvalPending.callId);
      } else if (input === 'd') {
        // Disable auto-approve (deny + stop asking)
        dispatch({ type: 'DISABLE_AUTO_APPROVE' });
        _deny(dispatch, sendMessage, state.approvalPending.callId);
      }
      return; // Don't let these fall through to input
    }
  });
}

function _approve(
  dispatch: React.Dispatch<import('../state/types.js').Action>,
  sendMessage: (msg: { type: string; [key: string]: unknown }) => void,
  callId: string,
) {
  sendMessage({
    type: 'tool_approval',
    id: callId,
    approved: true,
  });
  dispatch({ type: 'APPROVE_TOOL', callId });
}

function _deny(
  dispatch: React.Dispatch<import('../state/types.js').Action>,
  sendMessage: (msg: { type: string; [key: string]: unknown }) => void,
  callId: string,
) {
  sendMessage({
    type: 'tool_approval',
    id: callId,
    approved: false,
  });
  dispatch({ type: 'DENY_TOOL', callId });
}
