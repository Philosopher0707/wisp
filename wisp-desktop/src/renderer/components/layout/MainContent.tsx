import React from 'react';
import { useAppState } from '../../state/context.js';
import { TopBar } from '../topbar/TopBar.js';
import { ChatArea } from '../chat/ChatArea.js';
import { SuggestionPanel } from '../chat/SuggestionPanel.js';
import './MainContent.css';

export const MainContent: React.FC = () => {
  const { state } = useAppState();
  return (
    <main className="main-content">
      <TopBar />
      {state.suggestionsPanelOpen && <SuggestionPanel />}
      <ChatArea />
    </main>
  );
};
