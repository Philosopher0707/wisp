import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAppState } from '../../state/context.js';
import { useKeybindings } from '../../hooks/useKeybindings.js';
import { Sidebar } from './Sidebar.js';
import { MainContent } from './MainContent.js';
import { FileExplorer } from '../files/FileExplorer.js';
import { ApprovalPrompt } from '../ApprovalPrompt.js';
import { SearchModal } from '../SearchModal.js';
import { PluginsPanel } from '../PluginsPanel.js';
import { SettingsModal } from '../SettingsModal.js';
import { ShortcutModal } from '../ShortcutModal.js';
import { QuickFileModal } from '../QuickFileModal.js';
import { PlanPanel } from '../PlanPanel.js';
import { CheckpointPanel } from '../CheckpointPanel.js';
import { InlineEdit } from '../chat/InlineEdit.js';
import { ArenaPanel } from '../ArenaPanel.js';
import './AppShell.css';

const MIN_SIDEBAR = 180;
const MAX_SIDEBAR = 480;

function getSidebarWidth(): number {
  const stored = localStorage.getItem('wisp_sidebar_width');
  if (stored) {
    const n = parseInt(stored, 10);
    if (n >= MIN_SIDEBAR && n <= MAX_SIDEBAR) return n;
  }
  return 240;
}

export const AppShell: React.FC = () => {
  useKeybindings();
  const { state, dispatch } = useAppState();
  const [sidebarWidth, setSidebarWidth] = useState(getSidebarWidth);
  const dragging = useRef(false);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      const next = Math.min(MAX_SIDEBAR, Math.max(MIN_SIDEBAR, e.clientX));
      setSidebarWidth(next);
    };
    const onMouseUp = () => {
      if (dragging.current) {
        dragging.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        localStorage.setItem('wisp_sidebar_width', String(sidebarWidth));
      }
    };
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, [sidebarWidth]);

  return (
    <div className="app-shell" style={{ '--sidebar-width': `${sidebarWidth}px` } as React.CSSProperties}>
      <Sidebar />
      <div className="sidebar-resize-handle" onMouseDown={onMouseDown} />
      <MainContent />
      {state.rightPanelOpen && <FileExplorer />}
      {state.checkpointPanelOpen && <CheckpointPanel />}
      {state.approvalPending && <ApprovalPrompt />}
      {state.uiOverlay === 'search' && <SearchModal />}
      {state.uiOverlay === 'plugins' && <PluginsPanel />}
      {state.uiOverlay === 'settings' && <SettingsModal />}
      {state.uiOverlay === 'shortcuts' && <ShortcutModal />}
      {state.uiOverlay === 'quickfile' && <QuickFileModal />}
      {state.uiOverlay === 'inlineEdit' && (
        <InlineEdit visible onClose={() => dispatch({ type: 'CLOSE_OVERLAY' })} />
      )}
      {state.uiOverlay === 'arena' && <ArenaPanel />}
      {state.pendingPlan && <PlanPanel />}
    </div>
  );
};
