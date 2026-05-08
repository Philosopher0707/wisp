import { useEffect } from 'react';
import { useAppState } from '../state/context.js';

const FONT_SCALE_KEY = 'wisp_font_scale';
const MIN_SCALE = 0.8;
const MAX_SCALE = 1.6;
const STEP = 0.05;

function getScale(): number {
  const stored = localStorage.getItem(FONT_SCALE_KEY);
  if (stored) {
    const n = parseFloat(stored);
    if (n >= MIN_SCALE && n <= MAX_SCALE) return n;
  }
  return 1;
}

function setScale(s: number) {
  const clamped = Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));
  localStorage.setItem(FONT_SCALE_KEY, String(clamped));
  document.documentElement.style.setProperty('--font-scale', String(clamped));
}

// Init on load
if (typeof document !== 'undefined') {
  setScale(getScale());
}

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
            return;
          case 'k':
            e.preventDefault();
            dispatch({ type: 'OPEN_OVERLAY', overlay: 'search' });
            return;
          case 'p':
            e.preventDefault();
            dispatch({ type: 'OPEN_OVERLAY', overlay: 'quickfile' });
            return;
          case 'f':
            e.preventDefault();
            dispatch({ type: 'TOGGLE_CONV_SEARCH' });
            return;
          case '/':
            e.preventDefault();
            dispatch({ type: 'OPEN_OVERLAY', overlay: 'shortcuts' });
            return;
          case 'l':
            e.preventDefault();
            dispatch({ type: 'CLEAR_CHAT' });
            return;
          case 't':
            e.preventDefault();
            dispatch({ type: 'TOGGLE_THINKING' });
            return;
          case 'b':
            e.preventDefault();
            dispatch({ type: 'TOGGLE_RIGHT_PANEL' });
            return;
          case '=':  // Cmd+= (which is Cmd+Shift+= on US keyboards = Cmd+Plus)
          case '+':
            e.preventDefault();
            setScale(getScale() + STEP);
            return;
          case '-':
            e.preventDefault();
            setScale(getScale() - STEP);
            return;
          case '0':
            e.preventDefault();
            setScale(1);
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
