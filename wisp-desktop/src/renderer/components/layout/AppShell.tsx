import React from 'react';
import { useKeybindings } from '../../hooks/useKeybindings.js';
import { Sidebar } from './Sidebar.js';
import { MainContent } from './MainContent.js';
import './AppShell.css';

export const AppShell: React.FC = () => {
  useKeybindings();

  return (
    <div className="app-shell">
      <Sidebar />
      <MainContent />
    </div>
  );
};
