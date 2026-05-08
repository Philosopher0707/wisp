import React from 'react';
import { useAppState } from '../../state/context.js';
import { useKeybindings } from '../../hooks/useKeybindings.js';
import { Sidebar } from './Sidebar.js';
import { MainContent } from './MainContent.js';
import { ApprovalPrompt } from '../ApprovalPrompt.js';
import './AppShell.css';

export const AppShell: React.FC = () => {
  useKeybindings();
  const { state } = useAppState();

  return (
    <div className="app-shell">
      <Sidebar />
      <MainContent />
      {state.approvalPending && <ApprovalPrompt />}
    </div>
  );
};
