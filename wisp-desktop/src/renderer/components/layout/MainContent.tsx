import React from 'react';
import { TopBar } from '../topbar/TopBar.js';
import { ChatArea } from '../chat/ChatArea.js';
import './MainContent.css';

export const MainContent: React.FC = () => {
  return (
    <main className="main-content">
      <TopBar />
      <ChatArea />
    </main>
  );
};
