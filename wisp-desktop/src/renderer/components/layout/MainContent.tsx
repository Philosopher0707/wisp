import React from 'react';
import { useAppState } from '../../state/context.js';
import { TopBar } from '../topbar/TopBar.js';
import { ChatArea } from '../chat/ChatArea.js';
import { SuggestionPanel } from '../chat/SuggestionPanel.js';
import { ChatErrorBoundary } from '../ChatErrorBoundary.js';
import './MainContent.css';

export const MainContent: React.FC = () => {
  const { state, dispatch } = useAppState();
  return (
    <main className="main-content">
      <TopBar />
      {state.suggestionsPanelOpen && <SuggestionPanel />}
      <ChatErrorBoundary onNewChat={() => dispatch({ type: 'NEW_CHAT' })}>
        <ChatArea />
      </ChatErrorBoundary>
    </main>
  );
};
