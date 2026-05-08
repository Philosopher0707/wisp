import React from 'react';
import { useAppState } from '../state/context.js';
import './ShortcutModal.css';

const shortcuts = [
  { key: 'Cmd+N', desc: 'New chat' },
  { key: 'Cmd+P', desc: 'Quick open file' },
  { key: 'Cmd+K', desc: 'Command palette' },
  { key: 'Cmd+F', desc: 'Search in conversation' },
  { key: 'Cmd+L', desc: 'Clear chat' },
  { key: 'Cmd+T', desc: 'Toggle thinking' },
  { key: 'Cmd+B', desc: 'Toggle file explorer' },
  { key: 'Cmd+Plus', desc: 'Increase font size' },
  { key: 'Cmd+Minus', desc: 'Decrease font size' },
  { key: 'Cmd+0', desc: 'Reset font size' },
  { key: 'Cmd+C', desc: 'Stop generation' },
  { key: 'Cmd+/', desc: 'Show shortcuts' },
  { key: 'Esc', desc: 'Close dropdown / deny tool' },
  { key: 'Y', desc: 'Approve tool (when prompt shown)' },
  { key: 'N', desc: 'Deny tool (when prompt shown)' },
  { key: 'Enter', desc: 'Send message (Shift+Enter for newline)' },
];

export const ShortcutModal: React.FC = () => {
  const { dispatch } = useAppState();
  const close = () => dispatch({ type: 'CLOSE_OVERLAY' });

  return (
    <div className="overlay" onClick={close}>
      <div className="shortcut-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-header">
          <h2>Keyboard Shortcuts</h2>
          <button className="panel-close" onClick={close}>×</button>
        </div>
        <div className="shortcut-body">
          {shortcuts.map((s) => (
            <div key={s.key} className="shortcut-row">
              <kbd className="shortcut-key">{s.key}</kbd>
              <span className="shortcut-desc">{s.desc}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
