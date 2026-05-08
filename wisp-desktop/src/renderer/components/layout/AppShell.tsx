import React from 'react';
import { useAppState } from '../../state/context.js';
import { useKeybindings } from '../../hooks/useKeybindings.js';
import { Sidebar } from './Sidebar.js';
import { MainContent } from './MainContent.js';
import { ApprovalPrompt } from '../ApprovalPrompt.js';
import { SearchModal } from '../SearchModal.js';
import { PluginsPanel } from '../PluginsPanel.js';
import { SettingsModal } from '../SettingsModal.js';
import './AppShell.css';

export const AppShell: React.FC = () => {
  useKeybindings();
  const { state } = useAppState();

  return (
    <div className="app-shell">
      <Sidebar />
      <MainContent />
      {state.approvalPending && <ApprovalPrompt />}
      {state.uiOverlay === 'search' && <SearchModal />}
      {state.uiOverlay === 'plugins' && <PluginsPanel />}
      {state.uiOverlay === 'settings' && <SettingsModal />}
    </div>
  );
};
