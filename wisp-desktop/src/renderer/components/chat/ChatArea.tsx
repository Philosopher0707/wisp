import React from 'react';
import { WelcomeHeading } from './WelcomeHeading.js';
import { ChatInput } from './ChatInput.js';
import { ProjectContextBar } from './ProjectContextBar.js';
import './ChatArea.css';

export const ChatArea: React.FC = () => {
  return (
    <div className="chat-area">
      <div className="chat-area-center">
        <WelcomeHeading />
        <ChatInput />
        <ProjectContextBar />
      </div>
    </div>
  );
};
