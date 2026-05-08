import React from 'react';
import { useAppState } from '../../state/context.js';
import { Shield, SlidersHorizontal, Pencil, FileText, ChevronDown } from '../../icons/index.js';
import './PermissionSelector.css';

const MODES: { mode: 'full' | 'ask_all' | 'auto_edit' | 'read_only'; label: string; icon: React.FC<{ size?: number }>; desc: string }[] = [
  { mode: 'full', label: 'Full Access', icon: Shield, desc: 'Auto-execute all tools' },
  { mode: 'ask_all', label: 'Ask All', icon: SlidersHorizontal, desc: 'Approve every tool call' },
  { mode: 'auto_edit', label: 'Auto Edit', icon: Pencil, desc: 'Auto edits, ask for bash/git' },
  { mode: 'read_only', label: 'Read Only', icon: FileText, desc: 'Block writes, allow reads' },
];

export const PermissionSelector: React.FC = () => {
  const { state, dispatch } = useAppState();
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  const current = MODES.find((m) => m.mode === state.permissionMode) || MODES[0];
  const Icon = current.icon;

  React.useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div className="perm-selector" ref={ref}>
      <button
        className={`perm-badge perm-badge--${current.mode}`}
        onClick={() => setOpen(!open)}
        title={`Permission: ${current.label}`}
      >
        <Icon size={13} />
        <span>{current.label}</span>
        <ChevronDown size={10} />
      </button>
      {open && (
        <div className="perm-dropdown">
          {MODES.map((m) => {
            const MIcon = m.icon;
            return (
              <button
                key={m.mode}
                className={`perm-option ${m.mode === current.mode ? 'perm-option--active' : ''}`}
                onClick={() => {
                  dispatch({ type: 'SET_PERMISSION_MODE', mode: m.mode });
                  setOpen(false);
                }}
              >
                <MIcon size={14} />
                <span className="perm-option-label">{m.label}</span>
                <span className="perm-option-desc">{m.desc}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
