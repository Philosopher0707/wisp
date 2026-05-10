import { useEffect, useRef } from 'react';
import { useAppState } from '../state/context.js';
import { loadKeybindings, findMatchingAction } from '../utils/keybindings.js';

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

function executeAction(
  action: string,
  dispatch: (a: any) => void,
  sendMessage: (m: any) => void,
  state: any,
): void {
  switch (action) {
    case 'newChat':
      dispatch({ type: 'NEW_CHAT' });
      break;
    case 'search':
      dispatch({ type: 'OPEN_OVERLAY', overlay: 'search' });
      break;
    case 'quickfile':
      dispatch({ type: 'OPEN_OVERLAY', overlay: 'quickfile' });
      break;
    case 'convSearch':
      dispatch({ type: 'TOGGLE_CONV_SEARCH' });
      break;
    case 'shortcuts':
      dispatch({ type: 'OPEN_OVERLAY', overlay: 'shortcuts' });
      break;
    case 'settings':
      dispatch({ type: 'OPEN_OVERLAY', overlay: 'settings' });
      break;
    case 'clearChat':
      dispatch({ type: 'CLEAR_CHAT' });
      break;
    case 'toggleThinking':
      dispatch({ type: 'TOGGLE_THINKING' });
      break;
    case 'toggleSidebar':
      dispatch({ type: 'TOGGLE_SIDEBAR' });
      break;
    case 'toggleRightPanel':
      dispatch({ type: 'TOGGLE_RIGHT_PANEL' });
      break;
    case 'fontIncrease':
      setScale(getScale() + STEP);
      break;
    case 'fontDecrease':
      setScale(getScale() - STEP);
      break;
    case 'fontReset':
      setScale(1);
      break;
    case 'interrupt':
      if (state.isStreaming) {
        sendMessage({ type: 'interrupt' });
        dispatch({ type: 'INTERRUPT' });
      }
      break;
    case 'planMode':
      dispatch({ type: 'TOGGLE_PLAN_MODE' });
      break;
    case 'inlineEdit':
      dispatch({ type: 'OPEN_OVERLAY', overlay: 'inlineEdit' });
      break;
    case 'arena':
      dispatch({ type: 'OPEN_OVERLAY', overlay: 'arena' });
      break;
  }
}

export function useKeybindings() {
  const { state, dispatch, sendMessage } = useAppState();
  const sendMsgRef = useRef(sendMessage);
  sendMsgRef.current = sendMessage;
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    const bindings = loadKeybindings();
    dispatch({ type: 'SET_KEYBINDINGS', keybindings: bindings });
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const isMeta = e.metaKey || e.ctrlKey;
      const cur = stateRef.current;
      const send = sendMsgRef.current;

      // Approval keys — highest priority when approval is pending
      if (cur.approvalPending) {
        if (e.key === 'y' && !isMeta) {
          e.preventDefault();
          send({
            type: 'tool_approval',
            id: cur.approvalPending.callId,
            approved: true,
          });
          dispatch({ type: 'APPROVE_TOOL', callId: cur.approvalPending.callId });
          return;
        }
        if (e.key === 'n' && !isMeta) {
          e.preventDefault();
          send({
            type: 'tool_approval',
            id: cur.approvalPending.callId,
            approved: false,
            reason: 'User denied',
          });
          dispatch({ type: 'DENY_TOOL', callId: cur.approvalPending.callId });
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          send({
            type: 'tool_approval',
            id: cur.approvalPending.callId,
            approved: false,
            reason: 'User denied',
          });
          dispatch({ type: 'DENY_TOOL', callId: cur.approvalPending.callId });
          return;
        }
      }

      // Escape — close dropdown or overlay
      if (e.key === 'Escape') {
        if (cur.activeDropdown) {
          dispatch({ type: 'CLOSE_DROPDOWN' });
          return;
        }
        if (cur.uiOverlay) {
          dispatch({ type: 'CLOSE_OVERLAY' });
          return;
        }
        // If vim mode is on, let ChatInput handle Escape to exit insert mode
        // Don't prevent default here as it would block vim's Escape handler
        return;
      }

      // Use custom keybindings for Cmd/Ctrl shortcuts
      const keybindings = cur.keybindings && Object.keys(cur.keybindings).length > 0
        ? cur.keybindings
        : loadKeybindings();

      if (isMeta) {
        const matchedAction = findMatchingAction(e, keybindings);
        if (matchedAction) {
          e.preventDefault();
          executeAction(matchedAction, dispatch, send, cur);
          return;
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [state.approvalPending, state.activeDropdown, state.uiOverlay, state.isStreaming, state.keybindings, dispatch]);
}
