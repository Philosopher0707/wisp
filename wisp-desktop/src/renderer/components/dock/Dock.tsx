import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAppState } from '../../state/context.js';
import type { DockTab } from '../../state/types.js';
import { FileDiff, Terminal, Globe, Folder, X } from '../../icons/index.js';
import type { IconComponent } from '../../icons/index.js';
import { FileExplorer } from '../files/FileExplorer.js';
import { DiffTab } from './DiffTab.js';
import { TerminalTab } from './TerminalTab.js';
import { BrowserTab } from './BrowserTab.js';
import './Dock.css';

export const DOCK_TABS: Array<{ id: DockTab; label: string; icon: IconComponent }> = [
  { id: 'diff', label: 'Diff', icon: FileDiff },
  { id: 'terminal', label: 'Terminal', icon: Terminal },
  { id: 'browser', label: 'Browser', icon: Globe },
  { id: 'files', label: 'Files', icon: Folder },
];

const MIN_WIDTH = 320;
const DEFAULT_WIDTH = 480;
const MIN_MAIN = 420;
const STORAGE_KEY = 'wisp_dock_width';

function storedWidth(): number {
  try {
    const n = parseInt(localStorage.getItem(STORAGE_KEY) ?? '', 10);
    if (n >= MIN_WIDTH) return n;
  } catch {
    // storage unavailable: use the default
  }
  return DEFAULT_WIDTH;
}

export const Dock: React.FC = () => {
  const { state, dispatch } = useAppState();
  const [width, setWidth] = useState(storedWidth);
  const dragging = useRef(false);
  const latest = useRef(width);
  latest.current = width;

  // The dock may never squeeze the conversation below MIN_MAIN, nor outgrow a window that was resized smaller.
  const clamp = useCallback((w: number) => {
    const sidebar = state.sidebarCollapsed ? 0 : parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width'), 10) || 260;
    const max = Math.max(MIN_WIDTH, window.innerWidth - sidebar - MIN_MAIN);
    return Math.min(max, Math.max(MIN_WIDTH, w));
  }, [state.sidebarCollapsed]);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (dragging.current) setWidth(clamp(window.innerWidth - e.clientX));
    };
    const onUp = () => {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      try {
        localStorage.setItem(STORAGE_KEY, String(latest.current));
      } catch {
        // not persisted; fine
      }
    };
    const onResize = () => setWidth((w) => clamp(w));
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('resize', onResize);
    };
  }, [clamp]);

  const onHandleDown = (e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };

  return (
    <aside className="dock" style={{ width: clamp(width) }} aria-label="Workbench">
      <div className="dock-resize" onMouseDown={onHandleDown} role="separator" aria-orientation="vertical" />
      <div className="dock-header">
        <div className="dock-tabs" role="tablist">
          {DOCK_TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              role="tab"
              aria-selected={state.dockTab === id}
              className={`dock-tab${state.dockTab === id ? ' dock-tab--active' : ''}`}
              onClick={() => state.dockTab !== id && dispatch({ type: 'SET_DOCK_TAB', tab: id })}
              title={label}
            >
              <Icon size={14} />
              <span>{label}</span>
            </button>
          ))}
        </div>
        <button className="dock-tool-btn" onClick={() => dispatch({ type: 'TOGGLE_RIGHT_PANEL' })} title="Close panel" aria-label="Close panel">
          <X size={15} />
        </button>
      </div>
      <div className="dock-content">
        {state.dockTab === 'diff' && <DiffTab />}
        {state.dockTab === 'terminal' && <TerminalTab />}
        {state.dockTab === 'browser' && <BrowserTab />}
        {state.dockTab === 'files' && <div className="dock-files"><FileExplorer /></div>}
      </div>
    </aside>
  );
};
