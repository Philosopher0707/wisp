import { useEffect } from 'react';
import { useAppState } from '../state/context.js';

export function useKeybindings() {
  const { state, dispatch } = useAppState();

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const isMeta = e.metaKey || e.ctrlKey;

      // Escape — close dropdown or dismiss approval
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
            // Focus search — could open search modal
            return;
          case 'l':
            e.preventDefault();
            // Clear chat
            dispatch({ type: 'CLEAR_CHAT' });
            return;
          case 't':
            e.preventDefault();
            dispatch({ type: 'TOGGLE_THINKING' });
            return;
          case 'enter':
            e.preventDefault();
            // Submit with current input
            if (state.inputValue.trim()) {
              dispatch({ type: 'SUBMIT_MESSAGE', content: state.inputValue.trim() });
            }
            return;
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [state.activeDropdown, state.inputValue, dispatch]);
}
