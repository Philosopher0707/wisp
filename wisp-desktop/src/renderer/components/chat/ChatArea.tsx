import React, { useEffect, useRef } from 'react';
import { useAppState } from '../../state/context.js';
import { WelcomeHeading } from './WelcomeHeading.js';
import { ChatInput } from './ChatInput.js';
import { MessageBubble } from './MessageBubble.js';
import { ProjectContextBar } from './ProjectContextBar.js';
import './ChatArea.css';

export const ChatArea: React.FC = () => {
  const { state } = useAppState();
  const scrollRef = useRef<HTMLDivElement>(null);
  const hasMessages = state.messages.length > 0;

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.messages]);

  if (hasMessages) {
    return (
      <div className="chat-area chat-area--conversation">
        <div className="chat-messages" ref={scrollRef}>
          {state.messages.map((msg) => (
            <MessageBubble key={msg.id} msg={msg} />
          ))}
        </div>
        <div className="chat-input-sticky">
          <div className="chat-input-centered">
            <ChatInput />
            <ProjectContextBar />
          </div>
        </div>
      </div>
    );
  }

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
