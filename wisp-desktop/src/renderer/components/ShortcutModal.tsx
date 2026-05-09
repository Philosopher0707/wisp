import React from 'react';
import { useAppState } from '../state/context.js';
import { formatShortcut, DEFAULT_KEYBINDINGS, loadKeybindings } from '../utils/keybindings.js';
import './ShortcutModal.css';

const ACTION_LABELS: Record<string, string> = {
  'search': 'Command palette / Search',
  'quickfile': 'Quick open file',
  'settings': 'Open settings',
  'shortcuts': 'Show shortcuts',
  'newChat': 'New chat',
  'toggleSidebar': 'Toggle file explorer',
  'toggleRightPanel': 'Toggle right panel',
  'convSearch': 'Search in conversation',
  'clearChat': 'Clear chat',
  'toggleThinking': 'Toggle thinking',
  'fontIncrease': 'Increase font size',
  'fontDecrease': 'Decrease font size',
  'fontReset': 'Reset font size',
  'interrupt': 'Stop generation',
  'planMode': 'Toggle plan mode',
};

const STATIC_SHORTCUTS = [
  { key: 'Esc', desc: 'Close dropdown / deny tool' },
  { key: 'Y', desc: 'Approve tool (when prompt shown)' },
  { key: 'N', desc: 'Deny tool (when prompt shown)' },
  { key: 'Enter', desc: 'Send message (Shift+Enter for newline)' },
];

export const ShortcutModal: React.FC = () => {
  const { state, dispatch } = useAppState();
  const close = () => dispatch({ type: 'CLOSE_OVERLAY' });

  const bindings = state.keybindings && Object.keys(state.keybindings).length > 0
    ? state.keybindings
    : loadKeybindings();

  const dynamicShortcuts = Object.entries(ACTION_LABELS)
    .filter(([action]) => bindings[action])
    .map(([action, label]) => ({
      key: bindings[action],
      desc: label,
    }));

  return (
    <div className="overlay" onClick={close}>
      <div className="shortcut-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-header">
          <h2>Keyboard Shortcuts</h2>
          <button className="panel-close" onClick={close}>×</button>
        </div>
        <div className="shortcut-body">
          {dynamicShortcuts.map((s) => (
            <div key={s.key} className="shortcut-row">
              <kbd className="shortcut-key">{formatShortcut(s.key)}</kbd>
              <span className="shortcut-desc">{s.desc}</span>
            </div>
          ))}
          <div className="shortcut-divider" />
          {STATIC_SHORTCUTS.map((s) => (
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
